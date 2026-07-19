# app/api/auth.py
# -*- coding: utf-8 -*-
"""鉴权接口:注册(带邀请码)/ 登录 / 当前用户。

- 注册需填固定邀请码(config.invite_code),防止公网任意注册。
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
from app.config import get_settings
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
    settings = get_settings()
    if not settings.invite_code:
        raise HTTPException(status_code=403, detail="本站未开放注册")
    if req.invite_code.strip() != settings.invite_code:
        raise HTTPException(status_code=403, detail="邀请码不正确")

    uname = req.username.strip()
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(status_code=409, detail="该用户名已被注册")

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
    return TokenOut(
        token=build_token(user.id), username=user.username, is_admin=user.is_admin
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
