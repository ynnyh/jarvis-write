# app/api/projects/retrospective.py
# -*- coding: utf-8 -*-
"""书级复盘(docs/36 §4):读证据 → 反思提炼 → 按自治档位落库。

手动触发(作者在项目卡上点「复盘」),一次审校档调用。off 档直接 409——
开关的承诺是「停止采集」,这是进化系统最重要的边界。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import record_gen_inputs
from app.api.evolution import EvolutionMemoryOut
from app.auth import current_user_id
from app.db.models import EvolutionMemory
from app.db.session import get_db
from app.engines.evolution.evidence import gather_project_evidence
from app.engines.evolution.prefs import get_user_level
from app.engines.evolution.reflect import persist_memories, run_retrospective

from ._common import _get_project_or_404

router = APIRouter()


class RetrospectiveOut(BaseModel):
    level: str
    summary: str
    memories: list[EvolutionMemoryOut]


@router.post("/{project_id}/retrospective", response_model=RetrospectiveOut)
async def run_project_retrospective(
    project_id: int, db: Session = Depends(get_db)
) -> RetrospectiveOut:
    project = _get_project_or_404(db, project_id)
    uid = current_user_id.get()
    if uid is None:
        raise HTTPException(status_code=401, detail="未登录")
    level = get_user_level(db, uid)
    if level == "off":
        raise HTTPException(
            status_code=409,
            detail="进化开关未开启:先到 设置 → 进化 里选「确认后入库」或「自动入库」,再来复盘",
        )
    evidence = gather_project_evidence(db, project)
    if evidence["chapter_stats"]["approved"] == 0:
        raise HTTPException(
            status_code=400, detail="这本书还没有定稿章节:至少定稿一章,复盘才有东西可学"
        )
    # 带上已有记忆去重:同一句教训记两遍,注入时就是双倍权重
    existing = [
        row.content
        for row in db.query(EvolutionMemory.content)
        .filter(
            EvolutionMemory.user_id == uid,
            EvolutionMemory.status.in_(("pending", "confirmed")),
        )
        .all()
    ]
    try:
        summary, items, err = await run_retrospective(evidence, existing)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 失败原因直接反馈给作者
        raise HTTPException(status_code=502, detail=f"复盘失败:{exc}") from exc
    if err:
        raise HTTPException(status_code=502, detail=f"复盘失败:{err}")

    rows = persist_memories(
        db, user_id=uid, project_id=project.id, summary=summary,
        items=items, auto_confirm=(level == "auto"),
    )
    record_gen_inputs(
        kind="evolution-retrospective", project_id=project.id,
        payload={
            "level": level,
            "approved_chapters": evidence["chapter_stats"]["approved"],
            "memory_count": len(rows),
            "summary": summary,
        },
        db=db,
    )
    db.commit()
    for row in rows:
        db.refresh(row)
    return RetrospectiveOut(
        level=level, summary=summary,
        memories=[EvolutionMemoryOut.model_validate(r, from_attributes=True) for r in rows],
    )
