# app/api/admin.py
# -*- coding: utf-8 -*-
"""后台管理接口(阶段 9):用户列表 / 重置密码 / 禁用启用 / 删用户 / 邀请码。

GET    /api/admin/users                       全部用户(含项目数与用量汇总)
POST   /api/admin/users/{id}/reset-password   重置某用户密码
PATCH  /api/admin/users/{id}                  禁用 / 启用(不能禁用自己)
DELETE /api/admin/users/{id}                  删用户及其全部项目数据(不能删自己)
GET    /api/admin/invite-codes                邀请码列表(附旧单码回落状态)
POST   /api/admin/invite-codes                新建邀请码(可备注 / 限次)
PATCH  /api/admin/invite-codes/{id}           停用 / 启用某个邀请码
DELETE /api/admin/invite-codes/{id}           删除邀请码
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import delete_project_cascade
from app.auth import get_current_user, hash_password
from app.config import get_settings
from app.db.models import AppSetting, InviteCode, LlmUsage, Project, ProviderSetting, User
from app.db.session import get_db

logger = logging.getLogger("jarvis-write.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

_INVITE_CODE_KEY = "invite_code"


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """FastAPI 依赖:在校验登录的基础上要求管理员。"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def get_effective_invite_code(db: Session) -> tuple[str, str]:
    """当前生效的邀请码及其来源:DB 有记录(含空串)以 DB 为准,否则回落 .env。"""
    row = db.get(AppSetting, _INVITE_CODE_KEY)
    if row is not None:
        return row.value, "db"
    return get_settings().invite_code, "env"


# ---------- 用户管理 ----------


class AdminUserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: str
    project_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_calls: int


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """全部用户 + 项目数 + LLM 用量汇总(llm_usage 按 user_id 记账)。"""
    users = list(db.query(User).order_by(User.id))
    project_counts = dict(
        db.query(Project.user_id, func.count(Project.id))
        .group_by(Project.user_id)
        .all()
    )
    usage_rows = (
        db.query(
            LlmUsage.user_id,
            func.count(LlmUsage.id),
            func.sum(LlmUsage.prompt_tokens),
            func.sum(LlmUsage.completion_tokens),
        )
        .group_by(LlmUsage.user_id)
        .all()
    )
    usage = {
        uid: (int(calls or 0), int(prompt or 0), int(completion or 0))
        for uid, calls, prompt, completion in usage_rows
    }
    return [
        AdminUserOut(
            id=u.id,
            username=u.username,
            is_admin=u.is_admin,
            is_active=u.is_active,
            created_at=u.created_at.isoformat() if u.created_at else "",
            project_count=project_counts.get(u.id, 0),
            total_calls=usage.get(u.id, (0, 0, 0))[0],
            total_prompt_tokens=usage.get(u.id, (0, 0, 0))[1],
            total_completion_tokens=usage.get(u.id, (0, 0, 0))[2],
        )
        for u in users
    ]


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    req: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """管理员重置某用户密码(校验规则与注册一致)。"""
    user = _get_user_or_404(db, user_id)
    # bcrypt 只取密码前 72 字节,超长会直接抛 ValueError;提前拦截给明确提示
    if len(req.password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=400,
            detail="密码过长:按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)",
        )
    user.password_hash = hash_password(req.password)
    db.commit()
    logger.info("管理员重置了用户 %s 的密码", user.username)
    return {"ok": True}


class UserPatch(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: int,
    req: UserPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """禁用 / 启用账号。禁用后旧 token 立即失效(见 get_current_user)。"""
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id and not req.is_active:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    user.is_active = req.is_active
    db.commit()
    logger.info(
        "管理员%s了用户 %s", "启用" if req.is_active else "禁用", user.username
    )
    return {"ok": True, "is_active": req.is_active}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """删除用户:级联清掉其名下全部项目的关联数据,以及设置与用量记录。"""
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")

    deleted_projects = 0
    for project in db.query(Project).filter(Project.user_id == user.id).all():
        delete_project_cascade(db, project)
        deleted_projects += 1
    db.query(ProviderSetting).filter(
        ProviderSetting.user_id == user.id
    ).delete(synchronize_session=False)
    db.query(LlmUsage).filter(LlmUsage.user_id == user.id).delete(
        synchronize_session=False
    )
    db.delete(user)
    db.commit()
    logger.info(
        "管理员删除了用户 %s(含 %d 个项目)", user.username, deleted_projects
    )
    return {"ok": True, "deleted_projects": deleted_projects}


# ---------- 邀请码(多码体系) ----------


class InviteCodeItem(BaseModel):
    id: int
    code: str
    note: str | None
    max_uses: int | None
    used_count: int
    is_active: bool
    created_at: str


class LegacyFallback(BaseModel):
    """表为空时仍在生效的旧单码(app_settings / .env),前端用来提示过渡状态。"""

    code: str
    source: str  # db / env


class InviteCodeListOut(BaseModel):
    items: list[InviteCodeItem]
    legacy_fallback: LegacyFallback | None


class InviteCodeCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{4,64}$")
    note: str | None = Field(default=None, max_length=200)
    max_uses: int | None = Field(default=None, ge=1)


class InviteCodePatch(BaseModel):
    is_active: bool


def _to_item(row: InviteCode) -> InviteCodeItem:
    return InviteCodeItem(
        id=row.id,
        code=row.code,
        note=row.note,
        max_uses=row.max_uses,
        used_count=row.used_count,
        is_active=row.is_active,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/invite-codes", response_model=InviteCodeListOut)
async def list_invite_codes(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """邀请码列表;表为空时附当前生效的旧单码,便于前端提示过渡状态。"""
    rows = list(db.query(InviteCode).order_by(InviteCode.id))
    legacy = None
    if not rows:
        code, source = get_effective_invite_code(db)
        legacy = LegacyFallback(code=code, source=source)
    return InviteCodeListOut(items=[_to_item(r) for r in rows], legacy_fallback=legacy)


@router.post("/invite-codes", response_model=InviteCodeItem)
async def create_invite_code(
    req: InviteCodeCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    code = req.code.strip()
    if db.query(InviteCode).filter(InviteCode.code == code).first():
        raise HTTPException(status_code=400, detail="邀请码已存在")
    row = InviteCode(
        code=code,
        note=req.note.strip() if req.note else None,
        max_uses=req.max_uses,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("管理员创建了邀请码 %s(上限:%s)", row.code, row.max_uses or "不限")
    return _to_item(row)


def _get_invite_or_404(db: Session, invite_id: int) -> InviteCode:
    row = db.get(InviteCode, invite_id)
    if row is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    return row


@router.patch("/invite-codes/{invite_id}", response_model=InviteCodeItem)
async def patch_invite_code(
    invite_id: int,
    req: InviteCodePatch,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    row = _get_invite_or_404(db, invite_id)
    row.is_active = req.is_active
    db.commit()
    logger.info(
        "管理员%s了邀请码 %s", "启用" if req.is_active else "停用", row.code
    )
    return _to_item(row)


@router.delete("/invite-codes/{invite_id}")
async def delete_invite_code(
    invite_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    row = _get_invite_or_404(db, invite_id)
    db.delete(row)
    db.commit()
    logger.info("管理员删除了邀请码 %s", row.code)
    return {"ok": True}
