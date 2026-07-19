# app/auth.py
# -*- coding: utf-8 -*-
"""鉴权:密码哈希 / JWT / 当前用户依赖 + 上下文注入。

设计要点:
- 密码 bcrypt 哈希,不存明文。
- 登录发 JWT(HS256),前端存起来随 Authorization: Bearer 带上。
- 当前用户 id 用 contextvar 存一份:异步生成任务(asyncio.create_task)
  会自动继承上下文,于是深处的 LLM 工厂能取到"这个用户的 key",
  不必把 user_id 一层层传穿整个引擎。
"""
from __future__ import annotations

import contextvars
import datetime as _dt

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User
from app.db.session import get_db

# 当前请求/任务的用户 id;后台任务继承创建时的上下文
current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_user_id", default=None
)

_ALGO = "HS256"


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def build_token(user_id: int) -> str:
    settings = get_settings()
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + _dt.timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def _decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=[_ALGO]
        )
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


async def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """FastAPI 依赖:校验 token → 取用户 → 存进 contextvar。"""
    token = _bearer_token(request)
    uid = _decode_token(token) if token else None
    if uid is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    current_user_id.set(user.id)
    return user


def assert_project_owner(project) -> None:
    """数据隔离:项目必须属于当前用户,否则按"不存在"处理(不泄露存在性)。

    project 为 None 时直接跳过(由各接口自己的 404 分支处理)。
    """
    if project is None:
        return
    uid = current_user_id.get()
    owner = getattr(project, "user_id", None)
    if uid is None or owner != uid:
        raise HTTPException(status_code=404, detail="项目不存在")
