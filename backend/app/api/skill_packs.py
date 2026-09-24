# app/api/skill_packs.py
# -*- coding: utf-8 -*-
"""创作 Skill 包接口:列出 / 启停 / 编辑条目(版本化) / 回退历史版本(docs/21)。

GET   /api/skill-packs                    列出全部包(首次访问幂等 seed 官方包)
PATCH /api/skill-packs/{pack_id}          启停 / 改名 / 改条目(条目变更 version+1,旧版进 history)
POST  /api/skill-packs/{pack_id}/restore  回退到 history 里的某版内容(存为新版本)

is_builtin 只影响「随包分发」,不锁编辑——官方包同样可改可关(docs/21 风险 8)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import SkillPack
from app.db.session import get_db
from app.engines.skills.packs import (
    HISTORY_KEEP,
    ensure_builtin_packs,
    normalize_entries,
)

logger = logging.getLogger("jarvis-write.skill-packs")

router = APIRouter(
    prefix="/api/skill-packs",
    tags=["skill-packs"],
    dependencies=[Depends(get_current_user)],
)


class SkillEntryOut(BaseModel):
    node: str
    kind: str
    directive: str | None = None
    params: dict[str, str] | None = None
    ban_list: list[str] | None = None


class SkillPackOut(BaseModel):
    id: int
    pack_key: str
    name: str
    description: str
    scope: list[str]
    entries: list[dict]
    version: int
    history: list[dict]
    enabled: bool
    is_builtin: bool


class SkillPackPatch(BaseModel):
    """全字段可选:前端切开关只传 enabled;改条目才传 entries。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    entries: list[dict] | None = None


class RestoreIn(BaseModel):
    version: int


def _pack_or_404(db: Session, pack_id: int) -> SkillPack:
    pack = db.get(SkillPack, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"Skill 包 {pack_id} 不存在")
    return pack


def _out(pack: SkillPack) -> SkillPackOut:
    return SkillPackOut(
        id=pack.id,
        pack_key=pack.pack_key,
        name=pack.name,
        description=pack.description or "",
        scope=list(pack.scope or []),
        entries=list(pack.entries or []),
        version=pack.version or 1,
        history=list(pack.history or []),
        enabled=bool(pack.enabled),
        is_builtin=bool(pack.is_builtin),
    )


@router.get("", response_model=list[SkillPackOut])
async def list_packs(db: Session = Depends(get_db)):
    """全部包,官方在前(首次访问把官方包 seed 进库;幂等)。"""
    ensure_builtin_packs(db)
    packs = db.query(SkillPack).order_by(SkillPack.is_builtin.desc(), SkillPack.id).all()
    return [_out(p) for p in packs]


@router.patch("/{pack_id}", response_model=SkillPackOut)
async def patch_pack(pack_id: int, req: SkillPackPatch, db: Session = Depends(get_db)):
    """局部更新:启停随时可做;改条目则旧版存进 history(保留最近 HISTORY_KEEP 版)。"""
    pack = _pack_or_404(db, pack_id)
    if req.name is not None:
        pack.name = req.name.strip()
    if req.enabled is not None:
        pack.enabled = req.enabled
        logger.info("Skill 包《%s》%s", pack.name, "启用" if req.enabled else "停用")
    if req.entries is not None:
        try:
            fresh = normalize_entries(req.entries)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        history = [h for h in (pack.history or []) if isinstance(h, dict)]
        history.append({"version": pack.version or 1, "entries": list(pack.entries or [])})
        pack.history = history[-HISTORY_KEEP:]
        pack.entries = fresh
        pack.version = (pack.version or 1) + 1
        logger.info("Skill 包《%s》条目更新 → v%d", pack.name, pack.version)
    db.commit()
    db.refresh(pack)
    return _out(pack)


@router.post("/{pack_id}/restore", response_model=SkillPackOut)
async def restore_pack(pack_id: int, req: RestoreIn, db: Session = Depends(get_db)):
    """回退到历史某版的条目内容:当前版先入 history,回退内容存为新版本。"""
    pack = _pack_or_404(db, pack_id)
    snapshot = next(
        (h for h in (pack.history or [])
         if isinstance(h, dict) and h.get("version") == req.version),
        None,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"没有 v{req.version} 的历史版本")
    history = [h for h in (pack.history or []) if isinstance(h, dict)]
    history.append({"version": pack.version or 1, "entries": list(pack.entries or [])})
    pack.history = history[-HISTORY_KEEP:]
    pack.entries = [dict(e) for e in snapshot.get("entries") or []]
    pack.version = (pack.version or 1) + 1
    db.commit()
    db.refresh(pack)
    logger.info("Skill 包《%s》回退 v%d 内容 → 存为 v%d", pack.name, req.version, pack.version)
    return _out(pack)
