# app/api/auth.py
# -*- coding: utf-8 -*-
"""鉴权接口:注册(带邀请码)/ 登录 / 当前用户。

- 注册需填邀请码:数据库 app_settings 里的优先,无记录时回落 .env 的
  invite_code;两者皆空则关闭注册(见 admin 接口)。
- 登录返回 JWT,前端存起来随请求带上。
- 每个账号的 LLM key 独立(见 settings 接口),互不共用。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import (
    build_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.api.admin import get_effective_invite_code
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    invite_code: str = Field(min_length=1)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    username: str
    is_admin: bool


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool

    model_config = {"from_attributes": True}


@router.post("/register", response_model=TokenOut)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    # 邀请码:DB(app_settings)优先,无记录时回落 .env;空串 = 关闭注册
    invite_code, _source = get_effective_invite_code(db)
    if not invite_code:
        raise HTTPException(status_code=403, detail="本站未开放注册")
    if req.invite_code.strip() != invite_code:
        raise HTTPException(status_code=403, detail="邀请码不正确")

    uname = req.username.strip()
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(status_code=409, detail="该用户名已被注册")

    # bcrypt 只取密码前 72 字节,超长会直接抛 ValueError;提前拦截给明确提示
    if len(req.password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=400,
            detail="密码过长:按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)",
        )

    # 首个注册用户设为管理员(方便你自己接管);之后都是普通用户
    is_first = db.query(User).count() == 0
    user = User(
        username=uname,
        password_hash=hash_password(req.password),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(
        token=build_token(user.id), username=user.username, is_admin=user.is_admin
    )


@router.post("/login", response_model=TokenOut)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username.strip()).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用,请联系管理员")
    return TokenOut(
        token=build_token(user.id), username=user.username, is_admin=user.is_admin
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
