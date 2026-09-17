# Abook — 教材到考试的闭环 MVP

基于 Vue 3、TypeScript、FastAPI、MySQL、Celery 和 Redis。第一版仅接收 PDF，只生成与审核单选题。流程按 `AGENTS.md` 实现：目录发现与确认、章节分类、结构化学习、按章知识点审核与出题、组卷发布、收卷判分、错题重练。

## 启动

```bash
cp .env.example .env
docker compose up --build
```

打开 <http://localhost:5173>。首次用 `admin/admin12345678` 登录。admin 可完成上传、校对、审核、组卷、考试和错题练习全部流程，并在“账号管理”创建教师、学生账号。第一版教师只可阅读已索引教材；学生可阅读教材、参加考试和错题练习。系统不开放自助注册。部署前修改 `.env` 中的数据库密码、`ADMIN_PASSWORD` 和 `APP_SECRET`。

### 模型配置

首次启动会将环境变量中的默认模型写入数据库。默认连接**宿主机运行的 Ollama**：`MODEL_BASE_URL=http://host.docker.internal:11434/v1`，文本模型为 `qwen3-vl:8b`，扫描页 OCR 模型为 `qwen3-vl:4b`。Docker Compose 通过 `extra_hosts: host.docker.internal:host-gateway` 从普通桥接网络访问宿主机，未使用 host 网络模式。[Ollama 的 OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)支持此 `/v1/chat/completions` 地址；[Docker 的 host-gateway 映射](https://docs.docker.com/compose/how-tos/networking/)用于桥接容器访问宿主机。

admin 可在“模型接入”添加或编辑提供商，填写显示名称、API 根地址、接口类型和可选密钥。支持 Ollama 与提供 `/models`、`/chat/completions` 的 OpenAI 兼容云端接口。选择提供商后点击“获取支持的模型”，再分别选择文本模型和视觉 OCR 模型；“测试可用性”会向所选模型发送实际的文本或图片请求，测试通过后保存模型选择。后续也可直接向 `model_providers` 表添加提供商，刷新页面即可显示；云端密钥建议通过管理页面填写。密钥在数据库中加密，接口只返回是否已设置；更换 `APP_SECRET` 后需重新填写密钥。模型查询使用 Ollama 的模型列表接口或 [OpenAI 兼容的模型列表接口](https://platform.openai.com/docs/api-reference/models/list)。

如果 Ollama 只监听 `127.0.0.1:11434`，容器无法直接访问。仓库提供了一个仅监听 Docker 网关地址的[本机转发服务](scripts/ollama_bridge.py)，把容器请求转发给回环地址上的 Ollama；无需修改 Ollama 服务，也不使用 Docker host 网络模式。当前机器的 `docker0` 地址为 `172.17.0.1`，用户级 systemd 服务已启用。新环境可在仓库根目录执行：

```bash
systemctl --user link "$PWD/scripts/abook-ollama-bridge.service"
systemctl --user enable --now abook-ollama-bridge.service
curl http://172.17.0.1:11434/api/tags
```

如果项目路径或 `docker0` 地址不同，请先修改[服务文件](scripts/abook-ollama-bridge.service)中的路径或 `--bind` 地址。可以用 `systemctl --user status abook-ollama-bridge.service` 检查运行状态。若原有 `.env` 中模型项为空，Compose 的默认值仍会生效；已有数据库模型配置不会被环境变量覆盖。

Ollama 暂不可达时，PDF 的文本页仍可解析，扫描页会标出待处理问题；admin 可人工修正文稿、补充知识点和录入题目。AI 任务会记录明确错误。质量问题不会被静默跳过。

部分视觉模型在真实教材页上会忽略强制 JSON 格式，使 Ollama 的 OpenAI 兼容接口返回 500。系统会对 Ollama 视觉请求去掉强制格式重试一次；若模型返回的是页面文字，会按可见标题匹配章节并标记人工核对。模型服务再次失败时，页面会显示其具体错误和目录暂定章节。

## 工作顺序

1. admin 输入教材名称并上传 PDF，教材进入教材库，无需填写版本。可选填目录所在 PDF 页范围（如 `6-13`），或逐行提供 `层级|章节标题|PDF起始页` 的手工目录。相同名称和文件不能重复导入；系统内部保留文件指纹用于追溯。已有教材可以直接点“解析目录”，无需重新上传。
2. 系统优先读取 PDF 多级书签；无书签时解析目录页的章/项目及小标题，扫描版则用视觉模型寻找并读取目录。系统匹配标题与正文，推算 PDF 起始页；匹配不确定的章/项目会标出。admin 校对目录标题、层级和起始页，保存并确认。
3. 启动章节正文解析。系统按已确认目录划分章节，在每章起始页调用视觉模型核对分类；人工录入或起始页推算不确定时还核对相邻页。其他页面按目录范围归章，扫描页另做视觉 OCR。admin 查看冲突和 OCR 问题，修正后确认章节并建立索引。原文块保留 PDF 页码作为出处，页码不作为知识点生成单位。
4. 在“知识点审核”选择章节，按需总结**这一章**的知识点。系统按目录小节和较短的原文片段逐段提取定义、原理、步骤、条件及易混点；每条结果必须引用本章原文块。admin 逐条通过或退回。已有章节可再次点击总结以补充更细的知识点。无原文出处的人工补充可入库，但不能出题。
5. 在“试题审核”先从教材库选择教材，再选章节和目录标题、题数。勾选大标题会自动勾选其下的小标题；系统从所选范围的已审核知识点出题，优先分散覆盖不同小节与原文块，避免只取最早录入的几条。知识点页的“按本章目录出题”进入整章出题，“仅用此知识点出题”只生成单点题。左右两栏独立滚动；题干引用图号时展示关联的教材 PDF 原页图，审核员须检查图片是否对应题意、是否含答案。结构、答案、重复和本章原文逐字依据自动校验；原文逐字匹配失败会提示 admin，但 admin 人工确认后仍可通过并发布，审核记录保留该提示。
6. admin 选择教材章节和已审核考题，设置每题分值与难度筛选，按勾选的题目核对总分，设置考试时段并发布。发布时冻结题目、答案和评分规则。
7. admin 或学生在时段内答题，答案自动保存；提交或到时判分。成绩包含错题、解析、原文依据及知识点掌握情况。错题可用已审核原题重练；变式题生成后仍需 admin 审核通过。

已发布试卷可由 admin 修改考试时间；已有答卷时只能调整结束时间。admin 可提前终止考试，未交卷答卷会立即按已保存答案判分。试卷自然结束或被终止后才能从列表删除；删除保留试卷快照、审核记录和历史答卷。

admin 可在教材库删除教材，也可在“知识点审核”删除单条知识点。删除后记录退出教材库、知识点和当前题库，并停止相关后台任务；教材原文件、审核历史、已发布试卷快照和答卷仍保留用于追溯。删除后的相同 PDF 可以重新导入为新教材。

## 任务与数据

Celery worker 以 `WORKER_CONCURRENCY` 控制并发，任务有进度、取消标记、超时和指数退避重试。正文解析按页提交进度，知识点总结按章分批处理，因此失败后可续跑。任务键防止重复入队。人工编辑保留审计记录；修改原文或章节映射会使关联知识点与题目进入待复核状态。已发布试卷的快照不随题库变化。

开发版首次启动用 SQLAlchemy 建表。正式部署建议增加迁移、外部对象存储、反向代理与 HTTPS，并将卷、密钥和数据库纳入备份。API 文档在 <http://localhost:8000/docs>。

## 验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
cd frontend && npm install && npm run build
```

集成测试用 SQLite 和即时执行的 Celery 任务覆盖人工录入路径，不依赖外部模型。实际部署由 MySQL、Redis 和独立 worker 执行任务。自动题目校验采用结构、唯一性和原文逐字依据等确定性检查；语义正确性与是否超纲仍由审核员最终确认。
