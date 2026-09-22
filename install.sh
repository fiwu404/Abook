#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yaml"
REPO_URL="https://github.com/fiwu404/Abook"
REPO_BRANCH="main"
INIT_SERVICE="secret-init"
INIT_MARKER_KEY="cc.fiwu.abook.cleanup.role"
INIT_MARKER_VALUE="secret-init-v1"
WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-180}"
RUNNING_SERVICES=(mysql redis backend worker beat frontend)
COMPOSE=(docker compose --project-directory "${PROJECT_DIR}" -f "${COMPOSE_FILE}")
AFTER_PULL_ARG="--deploy-after-pull"

fail() {
  echo "部署失败：$*" >&2
  exit 1
}

normalize_repo_url() {
  local value="${1%/}"
  printf '%s\n' "${value%.git}"
}

if [[ "${1:-}" != "${AFTER_PULL_ARG}" ]]; then
  [[ "$#" -eq 0 ]] || fail "不支持的参数：$*"
  command -v git >/dev/null 2>&1 || fail "找不到 git 命令"
  git_root="$(git -C "${PROJECT_DIR}" rev-parse --show-toplevel 2>/dev/null)" || fail "当前目录不是 Git 仓库"
  git_root="$(cd -- "${git_root}" && pwd -P)"
  [[ "${git_root}" == "${PROJECT_DIR}" ]] || fail "install.sh 必须位于 Git 仓库根目录"
  branch="$(git -C "${PROJECT_DIR}" symbolic-ref --quiet --short HEAD)" || fail "当前处于 detached HEAD，无法安全更新"
  [[ "${branch}" == "${REPO_BRANCH}" ]] || fail "当前分支是 ${branch}，要求分支为 ${REPO_BRANCH}"
  origin_url="$(git -C "${PROJECT_DIR}" remote get-url origin 2>/dev/null)" || fail "没有配置 origin 远程仓库"
  [[ "$(normalize_repo_url "${origin_url}")" == "$(normalize_repo_url "${REPO_URL}")" ]] || \
    fail "origin 地址不匹配：${origin_url}"
  tracked_changes="$(git -C "${PROJECT_DIR}" status --porcelain --untracked-files=no)"
  [[ -z "${tracked_changes}" ]] || fail "存在未提交的已跟踪文件修改，请先提交或还原后再部署"

  echo "正在从 ${REPO_URL} 更新 ${REPO_BRANCH}……"
  git -C "${PROJECT_DIR}" pull --ff-only origin "${REPO_BRANCH}"
  [[ -x "${PROJECT_DIR}/install.sh" ]] || fail "更新后的 install.sh 不存在或不可执行"
  exec "${PROJECT_DIR}/install.sh" "${AFTER_PULL_ARG}"
fi
shift
[[ "$#" -eq 0 ]] || fail "不支持的参数：$*"

command -v docker >/dev/null 2>&1 || fail "找不到 docker 命令"
[[ "${WAIT_TIMEOUT}" =~ ^[1-9][0-9]*$ ]] || fail "DEPLOY_WAIT_TIMEOUT 必须是正整数"
[[ -f "${COMPOSE_FILE}" ]] || fail "找不到 ${COMPOSE_FILE}"

mapfile -t configured_services < <("${COMPOSE[@]}" config --services)
init_matches=0
for service in "${configured_services[@]}"; do
  [[ "${service}" == "${INIT_SERVICE}" ]] && ((init_matches += 1))
done
[[ "${init_matches}" -eq 1 ]] || fail "Compose 中必须且只能存在一个精确名为 ${INIT_SERVICE} 的服务"

echo "正在构建并启动 Abook……"
"${COMPOSE[@]}" up -d --build

deadline=$((SECONDS + WAIT_TIMEOUT))
while true; do
  pending=()
  for service in "${RUNNING_SERVICES[@]}"; do
    mapfile -t ids < <("${COMPOSE[@]}" ps --quiet "${service}")
    if [[ "${#ids[@]}" -ne 1 ]]; then
      pending+=("${service}:容器数量=${#ids[@]}")
      continue
    fi
    state="$(docker inspect --format '{{.State.Status}}' "${ids[0]}")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${ids[0]}")"
    if [[ "${state}" != "running" || ("${health}" != "none" && "${health}" != "healthy") ]]; then
      pending+=("${service}:${state}/${health}")
    fi
  done

  if [[ "${#pending[@]}" -eq 0 ]]; then
    break
  fi
  if ((SECONDS >= deadline)); then
    "${COMPOSE[@]}" ps >&2 || true
    fail "等待服务就绪超时：${pending[*]}"
  fi
  sleep 2
done

mapfile -t init_ids < <("${COMPOSE[@]}" ps --all --quiet "${INIT_SERVICE}")
[[ "${#init_ids[@]}" -eq 1 ]] || fail "拒绝清理：当前项目的 ${INIT_SERVICE} 容器数量为 ${#init_ids[@]}"
init_id="${init_ids[0]}"
[[ "${init_id}" =~ ^[0-9a-f]{12,64}$ ]] || fail "拒绝清理：容器 ID 格式异常"

actual_service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${init_id}")"
actual_marker="$(docker inspect --format "{{index .Config.Labels \"${INIT_MARKER_KEY}\"}}" "${init_id}")"
actual_workdir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${init_id}")"
init_state="$(docker inspect --format '{{.State.Status}}' "${init_id}")"
init_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "${init_id}")"

[[ "${actual_service}" == "${INIT_SERVICE}" ]] || fail "拒绝清理：服务标签不匹配"
[[ "${actual_marker}" == "${INIT_MARKER_VALUE}" ]] || fail "拒绝清理：专用清理标记不匹配"
[[ "${actual_workdir}" == "${PROJECT_DIR}" ]] || fail "拒绝清理：Compose 项目目录不匹配"
[[ "${init_state}" == "exited" && "${init_exit_code}" == "0" ]] || \
  fail "拒绝清理：${INIT_SERVICE} 尚未成功退出（${init_state}/${init_exit_code}）"

docker container rm "${init_id}" >/dev/null
echo "部署完成：所有常驻服务已运行，${INIT_SERVICE} 已按容器 ID 精确删除。"
"${COMPOSE[@]}" ps
