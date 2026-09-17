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
from .models import User


SECRET = os.getenv("APP_SECRET", "")


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


def issue_token(user: User) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"id": user.id, "exp": int(time.time()) + 86400}).encode()).decode().rstrip("=")
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
        if user:
            return user
    except (ValueError, KeyError, TypeError):
        pass
    raise HTTPException(401, "请先登录")


def roles(*allowed):
    def dependency(user: User = Depends(current_user)):
        if user.role not in allowed:
            raise HTTPException(403, "无权执行此操作")
        return user
    return dependency


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
