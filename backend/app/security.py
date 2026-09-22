import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User, UserPermission, UserSecurity


SECRET = os.getenv("APP_SECRET", "")

PERMISSION_CATALOG = [
    {"code": "books.view", "group": "教材", "name": "查看教材与学习内容", "description": "查看已建立索引的教材、章节和结构化学习页"},
    {"code": "books.manage", "group": "教材", "name": "导入与解析教材", "description": "上传、删除、解析教材并校对目录、页码和章节"},
    {"code": "knowledge.view", "group": "知识点", "name": "查看知识点", "description": "查看按章节整理的知识点及原文依据"},
    {"code": "knowledge.edit", "group": "知识点", "name": "总结与编辑知识点", "description": "调用模型总结，并新增、修改或删除知识点"},
    {"code": "knowledge.review", "group": "知识点", "name": "审核知识点", "description": "通过或退回待审核知识点"},
    {"code": "questions.view", "group": "试题", "name": "查看试题", "description": "查看章节试题、答案、解析和依据"},
    {"code": "questions.edit", "group": "试题", "name": "生成与编辑试题", "description": "按章节生成，并新增、修改和重新校验试题"},
    {"code": "questions.review", "group": "试题", "name": "审核试题", "description": "通过或退回已校验试题"},
    {"code": "papers.view", "group": "试卷", "name": "查看试卷", "description": "查看有权访问的试卷及其状态"},
    {"code": "papers.manage", "group": "试卷", "name": "组卷与考试管理", "description": "创建、发布、改期、终止和删除试卷"},
    {"code": "jobs.view", "group": "系统", "name": "查看后台任务", "description": "查看解析、总结和出题任务进度"},
    {"code": "jobs.manage", "group": "系统", "name": "管理后台任务", "description": "取消、重试和清空后台任务"},
    {"code": "models.view", "group": "系统", "name": "查看模型配置", "description": "查看模型提供商和当前模型配置"},
    {"code": "models.manage", "group": "系统", "name": "管理模型接入", "description": "新增、修改、测试提供商并切换模型"},
    {"code": "users.view", "group": "账号", "name": "查看用户", "description": "查看教师、学生及其权限"},
    {"code": "users.manage", "group": "账号", "name": "管理用户与权限", "description": "创建、更名或重置教师和学生账号，并修改其权限"},
    {"code": "audits.view", "group": "系统", "name": "查看审计记录", "description": "查看关键内容和配置的操作历史"},
    {"code": "exams.take", "group": "学习", "name": "参加考试", "description": "进入考试、保存答案并交卷"},
    {"code": "results.view", "group": "学习", "name": "查看成绩与错题", "description": "查看本人答卷、成绩和知识点掌握情况"},
    {"code": "practice.use", "group": "学习", "name": "错题与变式练习", "description": "使用已审核原题重练或申请变式练习"},
]
PERMISSION_CODES = {item["code"] for item in PERMISSION_CATALOG}
PERMISSION_MARKER = "__configured__"
PERMISSION_IMPLICATIONS = {
    "books.manage": {"books.view"},
    "knowledge.view": {"books.view"},
    "knowledge.edit": {"knowledge.view", "books.view"},
    "knowledge.review": {"knowledge.view", "books.view"},
    "questions.view": {"knowledge.view", "books.view"},
    "questions.edit": {"questions.view", "knowledge.view", "books.view"},
    "questions.review": {"questions.view", "knowledge.view", "books.view"},
    "papers.manage": {"papers.view", "questions.view", "knowledge.view", "books.view"},
    "jobs.manage": {"jobs.view"},
    "models.manage": {"models.view"},
    "users.manage": {"users.view"},
    "exams.take": {"papers.view", "books.view"},
    "results.view": {"papers.view", "books.view"},
    "practice.use": {"results.view", "papers.view", "books.view"},
}
ROLE_DEFAULTS = {
    "teacher": {"books.view", "papers.view"},
    "student": {"books.view", "papers.view", "exams.take", "results.view", "practice.use"},
}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}:{digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(":")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def user_security(db: Session, user: User) -> UserSecurity:
    state = db.get(UserSecurity, user.id)
    if not state:
        state = UserSecurity(user_id=user.id)
        db.add(state)
        db.flush()
    return state


def issue_token(user: User, token_version: int = 0) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"id": user.id, "v": token_version,
        "exp": int(time.time()) + 86400}).encode()).decode().rstrip("=")
    signature = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    try:
        payload, signature = token.split(".")
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["exp"] < time.time():
            raise ValueError()
        user = db.get(User, data["id"])
        if user and int(data.get("v", 0)) == user_security(db, user).token_version:
            return user
    except (ValueError, KeyError, TypeError):
        pass
    raise HTTPException(401, "请先登录")


def normalize_permissions(values) -> set[str]:
    result = {value for value in values if value in PERMISSION_CODES}
    while True:
        expanded = result | set().union(*(PERMISSION_IMPLICATIONS.get(value, set()) for value in result))
        if expanded == result:
            return result
        result = expanded


def user_permissions(db: Session, user: User) -> set[str]:
    if user.role == "admin":
        return set(PERMISSION_CODES)
    return {value for value in db.scalars(select(UserPermission.permission).where(
        UserPermission.user_id == user.id, UserPermission.permission != PERMISSION_MARKER)).all()
            if value in PERMISSION_CODES}


def has_permission(db: Session, user: User, value: str) -> bool:
    return user.role == "admin" or value in user_permissions(db, user)


def set_user_permissions(db: Session, user: User, values) -> set[str]:
    requested = list(values)
    unknown = sorted(set(requested) - PERMISSION_CODES)
    if unknown:
        raise HTTPException(400, f"未知权限：{', '.join(unknown)}")
    granted = normalize_permissions(requested)
    for row in db.scalars(select(UserPermission).where(UserPermission.user_id == user.id)).all():
        db.delete(row)
    db.add(UserPermission(user_id=user.id, permission=PERMISSION_MARKER))
    for value in sorted(granted):
        db.add(UserPermission(user_id=user.id, permission=value))
    return granted


def permits(*allowed):
    def dependency(user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user_security(db, user).must_change_password:
            raise HTTPException(403, "请先修改初始密码")
        if user.role == "admin" or user_permissions(db, user).intersection(allowed):
            return user
        raise HTTPException(403, "当前账号缺少执行此操作的权限")
    return dependency


def initialize_permissions(db: Session):
    configured = set(db.scalars(select(UserPermission.user_id).where(
        UserPermission.permission == PERMISSION_MARKER)).all())
    for user in db.scalars(select(User).where(User.role != "admin")).all():
        if user.id not in configured:
            set_user_permissions(db, user, ROLE_DEFAULTS.get(user.role, set()))
    for user in db.scalars(select(User)).all():
        user_security(db, user)
    db.commit()


def bootstrap(db: Session):
    name, password = os.getenv("ADMIN_USER", "admin"), os.getenv("ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError("请设置 ADMIN_PASSWORD")
    existing = db.scalar(select(User).where(User.username == name))
    if existing and existing.role != "admin":
        raise RuntimeError("ADMIN_USER 已被其他角色占用，请修改该配置")
    if not existing:
        db.add(User(username=name, password_hash=hash_password(password), role="admin"))
    db.commit()
