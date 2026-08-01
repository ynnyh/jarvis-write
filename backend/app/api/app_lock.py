# app/api/app_lock.py
# -*- coding: utf-8 -*-
"""应用锁(仅 local 桌面单机模式):给本机 app 加一道启动密码。

威胁模型:防家人/同事的休闲锁,不要求抵御本地数据被直接读取——
故不发 token、不加密数据,解锁仅是一次性密码校验,前端用 sessionStorage
记住"本次会话已解锁"。

存储:app_settings KV 表,key=app_lock_password_hash,值为 bcrypt 哈希;
无记录/空串 = 未设锁。不复用 users.password_hash:本地用户由迁移创建时
已带默认密码哈希,无法区分"未设锁";且应用锁是单机 app 级设置而非用户
属性,放站点 KV 表语义最贴合,也无需迁移(app_settings 表本就有)。

忘记密码:/reset 提供无需旧密码的重置口子(须输入「重置」二字防误触)。
设计理由:威胁模型是休闲锁,本机访问者本就可以手动删 app_settings 记录
绕过锁,UI 口子只是把它产品化;重置后锁消失,用户进设置页能发现,不存在
静默失锁的安全退化。

server 模式:有自己的账号密码体系,应用锁无意义,全部接口按 404 处理。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.config import get_settings
from app.db.models import AppSetting
from app.db.session import get_db

router = APIRouter(prefix="/api/app-lock", tags=["app-lock"])

_LOCK_KEY = "app_lock_password_hash"


def _local_only() -> None:
    """server 模式没有应用锁概念:按 404 处理,不暴露存在性。"""
    if not get_settings().is_local:
        raise HTTPException(status_code=404, detail="Not Found")


def _stored_hash(db: Session) -> str:
    row = db.get(AppSetting, _LOCK_KEY)
    return row.value if row is not None else ""


def has_lock(db: Session) -> bool:
    """是否已设锁(/api/mode 也用它,前端启动时据此决定是否出锁屏)。"""
    return bool(_stored_hash(db))


class LockStatusOut(BaseModel):
    has_lock: bool


@router.get("/status", response_model=LockStatusOut)
async def lock_status(db: Session = Depends(get_db)):
    _local_only()
    return LockStatusOut(has_lock=has_lock(db))


class SetLockRequest(BaseModel):
    # 首次设锁不传;已有锁时必传且须正确
    old_password: str | None = Field(default=None, max_length=128)
    # 密码规则与注册/改密一致(6-128 位,另有 72 字节上限)
    new_password: str = Field(min_length=6, max_length=128)


@router.post("")
async def set_lock(req: SetLockRequest, db: Session = Depends(get_db)):
    """设置/修改应用锁密码。首次设置无需旧密码;已设锁后修改须验旧密码。"""
    _local_only()
    stored = _stored_hash(db)
    if stored and (
        not req.old_password or not verify_password(req.old_password, stored)
    ):
        raise HTTPException(status_code=400, detail="旧密码不正确")
    if stored and req.new_password == req.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
    # bcrypt 只取密码前 72 字节,超长会直接抛 ValueError;提前拦截给明确提示
    if len(req.new_password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=400,
            detail="密码过长:按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)",
        )
    row = db.get(AppSetting, _LOCK_KEY)
    if row is None:
        row = AppSetting(key=_LOCK_KEY, value="")
        db.add(row)
    row.value = hash_password(req.new_password)
    db.commit()
    return {"ok": True}


class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post("/unlock")
async def unlock(req: UnlockRequest, db: Session = Depends(get_db)):
    """锁屏解锁校验:对则 200,错则 401。只校验,不发任何凭证(休闲锁)。"""
    _local_only()
    stored = _stored_hash(db)
    if not stored:
        raise HTTPException(status_code=400, detail="尚未设置应用锁")
    if not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="密码不正确")
    return {"ok": True}


@router.post("/remove")
async def remove_lock(req: UnlockRequest, db: Session = Depends(get_db)):
    """移除应用锁:须验当前锁密码,恢复无锁状态。"""
    _local_only()
    stored = _stored_hash(db)
    if not stored:
        raise HTTPException(status_code=400, detail="尚未设置应用锁")
    if not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="密码不正确")
    db.query(AppSetting).filter(AppSetting.key == _LOCK_KEY).delete(
        synchronize_session=False
    )
    db.commit()
    return {"ok": True}


class ResetLockRequest(BaseModel):
    # 防误触:必须原样输入「重置」二字
    confirm: str = Field(min_length=1, max_length=20)


@router.post("/reset")
async def reset_lock(req: ResetLockRequest, db: Session = Depends(get_db)):
    """忘记密码的重置口子:无需旧密码,确认后直接清除锁记录。

    与 /remove 的区别:remove 是"记得密码、主动不要锁";reset 是"忘了密码"
    的兜底。为何敢免验:见模块 docstring(休闲锁威胁模型)。
    """
    _local_only()
    if req.confirm != "重置":
        raise HTTPException(status_code=400, detail="请输入「重置」二字确认")
    db.query(AppSetting).filter(AppSetting.key == _LOCK_KEY).delete(
        synchronize_session=False
    )
    db.commit()
    return {"ok": True}
