# app/api/evolution.py
# -*- coding: utf-8 -*-
"""进化系统 API(docs/36):用户级自治旋钮 + 记忆资产的查/改/删。

记忆是**用户资产**:所有查询与写操作都按 current_user_id 过滤,跨用户一律 404
(不泄露存在性,与项目归属同一口径)。off 档只影响采集与注入,不锁这里的管理
操作——关了旋钮仍能看到并删除历史记忆(资产保留,增量停止)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user_id, get_current_user
from app.db.models import EvolutionMemory
from app.db.session import get_db
from app.engines.evolution.prefs import LEVELS, get_user_level, set_user_level

router = APIRouter(
    prefix="/api/evolution",
    tags=["evolution"],
    dependencies=[Depends(get_current_user)],
)

# 管理操作允许把条目挪到这两种状态;pending 只能由复盘产生,API 不给设
_MUTABLE_STATUS = ("confirmed", "dismissed")
_LIST_CAP = 200


def _require_uid() -> int:
    uid = current_user_id.get()
    if uid is None:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


class EvolutionConfigOut(BaseModel):
    level: str


class EvolutionConfigIn(BaseModel):
    level: str = Field(min_length=2, max_length=10)


class EvolutionMemoryOut(BaseModel):
    id: int
    kind: str
    content: str
    evidence: str
    confidence: str
    status: str
    project_id: int | None
    source: dict[str, Any]
    created_at: datetime


class MemoryListOut(BaseModel):
    items: list[EvolutionMemoryOut]


class MemoryStatusIn(BaseModel):
    status: str = Field(min_length=5, max_length=20)


@router.get("/config", response_model=EvolutionConfigOut)
def get_evolution_config(db: Session = Depends(get_db)) -> EvolutionConfigOut:
    return EvolutionConfigOut(level=get_user_level(db, _require_uid()))


@router.put("/config", response_model=EvolutionConfigOut)
def put_evolution_config(
    req: EvolutionConfigIn, db: Session = Depends(get_db)
) -> EvolutionConfigOut:
    uid = _require_uid()
    if req.level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"档位只能是 {'/'.join(LEVELS)}")
    set_user_level(db, uid, req.level)
    db.commit()
    return EvolutionConfigOut(level=req.level)


@router.get("/memories", response_model=MemoryListOut)
def list_memories(
    status: str | None = None, db: Session = Depends(get_db)
) -> MemoryListOut:
    uid = _require_uid()
    query = db.query(EvolutionMemory).filter(EvolutionMemory.user_id == uid)
    if status:
        query = query.filter(EvolutionMemory.status == status)
    rows = query.order_by(EvolutionMemory.id.desc()).limit(_LIST_CAP).all()
    return MemoryListOut(items=[EvolutionMemoryOut.model_validate(r, from_attributes=True) for r in rows])


def _get_own_memory(db: Session, uid: int, memory_id: int) -> EvolutionMemory:
    row = db.get(EvolutionMemory, memory_id)
    if row is None or row.user_id != uid:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return row


@router.patch("/memories/{memory_id}", response_model=EvolutionMemoryOut)
def patch_memory(
    memory_id: int, req: MemoryStatusIn, db: Session = Depends(get_db)
) -> EvolutionMemoryOut:
    uid = _require_uid()
    if req.status not in _MUTABLE_STATUS:
        raise HTTPException(status_code=422, detail="状态只能是 confirmed/dismissed")
    row = _get_own_memory(db, uid, memory_id)
    row.status = req.status
    db.commit()
    db.refresh(row)
    return EvolutionMemoryOut.model_validate(row, from_attributes=True)


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    uid = _require_uid()
    row = _get_own_memory(db, uid, memory_id)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/memories/clear")
def clear_memories(db: Session = Depends(get_db)) -> dict[str, int]:
    """一键清空:作者有权让系统「忘掉一切」。立即生效于下一次注入。"""
    uid = _require_uid()
    deleted = (
        db.query(EvolutionMemory)
        .filter(EvolutionMemory.user_id == uid)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "deleted": int(deleted)}
