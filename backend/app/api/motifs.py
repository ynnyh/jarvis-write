# app/api/motifs.py
# -*- coding: utf-8 -*-
"""桥段台账 + 雷区清单接口:跨章重复描写的治理入口。

GET    /api/projects/{id}/motifs                 台账聚合 + 雷区清单(一次拉全)
POST   /api/projects/{id}/motifs/banned          登记雷区(同标签幂等,全书生效)
DELETE /api/projects/{id}/motifs/banned/{mid}    撤销雷区(台账历史不受影响)
POST   /api/projects/{id}/motifs/banned/promote  把台账既有标签升格为雷区
DELETE /api/projects/{id}/motifs/ledger?label=   清除某标签的台账历史(判定抽错/有意母题)
POST   /api/projects/{id}/motifs/scan-async      全书扫描:存量章节批量回填台账(后台任务)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, WritingMotif
from app.db.session import SessionLocal, get_db
from app.engines.consistency import motifs as motifs_engine
from app.jobs import list_running, spawn_job

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["motifs"],
    dependencies=[Depends(get_current_user)],
)


class BannedMotifOut(BaseModel):
    id: int
    label: str
    detail: str


class LedgerMotifOut(BaseModel):
    label: str
    detail: str
    chapters: list[int]
    count: int


class MotifsOut(BaseModel):
    banned: list[BannedMotifOut]
    ledger: list[LedgerMotifOut]


class BannedUpsertRequest(BaseModel):
    label: str = Field(min_length=2, max_length=100, description="短标签,如:铁锈玫瑰")
    detail: str = Field(default="", max_length=500, description="一句话说明(可选)")


class PromoteRequest(BaseModel):
    label: str = Field(min_length=2, max_length=100)


def _chapter_job_busy(project_id: int) -> str:
    """章节级任务互斥(与 editorial._book_job_busy 同口径,另含桥段扫描自身)。"""
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
        + list_running(f"diag-{project_id}")
        + list_running(f"rulescan-{project_id}")
        + list_running(f"contract-backfill-{project_id}")
    )
    return busy[0][1]["stage"] if busy else ""


@router.get("/motifs", response_model=MotifsOut)
async def get_motifs(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return MotifsOut(
        banned=[
            BannedMotifOut(id=r.id, label=r.label, detail=r.detail)
            for r in motifs_engine.banned_rows(db, project_id)
        ],
        ledger=[
            LedgerMotifOut(**it) for it in motifs_engine.ledger(db, project_id)
        ],
    )


@router.post("/motifs/banned")
async def add_banned(project_id: int, req: BannedUpsertRequest, db: Session = Depends(get_db)):
    """登记/更新雷区:一次标注,后续所有章节的生成全链路都规避。"""
    get_project_or_404(db, project_id)
    row = motifs_engine.add_banned(db, project_id, req.label, req.detail)
    db.commit()
    return {"id": row.id, "label": row.label, "detail": row.detail}


@router.delete("/motifs/banned/{motif_id}")
async def delete_banned(project_id: int, motif_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    if not motifs_engine.remove_banned(db, project_id, motif_id):
        raise HTTPException(status_code=404, detail="雷区不存在或已撤销")
    db.commit()
    return {"ok": True}


@router.post("/motifs/banned/promote")
async def promote_banned(project_id: int, req: PromoteRequest, db: Session = Depends(get_db)):
    """台账里的既有标签一键升格为雷区(说明沿用台账最近一次记录)。"""
    get_project_or_404(db, project_id)
    row = motifs_engine.promote_to_banned(db, project_id, req.label)
    if row is None:
        raise HTTPException(status_code=404, detail="台账里没有这个标签")
    db.commit()
    return {"id": row.id, "label": row.label, "detail": row.detail}


@router.delete("/motifs/ledger")
async def clear_ledger_label(project_id: int, label: str, db: Session = Depends(get_db)):
    """清除某标签的台账历史:判定抽错了、或它是有意的主母题(不再提醒复现)。"""
    get_project_or_404(db, project_id)
    key = motifs_engine._norm_label(label)
    olds = (
        db.query(WritingMotif)
        .filter(WritingMotif.project_id == project_id, WritingMotif.banned.is_(False))
        .all()
    )
    removed = 0
    for r in olds:
        if motifs_engine._norm_label(r.label) == key:
            db.delete(r)
            removed += 1
    db.commit()
    if not removed:
        raise HTTPException(status_code=404, detail="台账里没有这个标签")
    return {"removed": removed}


@router.post("/motifs/scan-async")
async def scan_motifs_async(project_id: int, db: Session = Depends(get_db)):
    """全书扫描:逐批抽取历史章节的描写母题回填台账(幂等,可重跑)。

    与章节级任务互斥——扫描期间生成/放行/体检会被拒,反之亦然;
    重复提交时直接复用进行中的任务。
    """
    get_project_or_404(db, project_id)
    running = list_running(f"motifscan-{project_id}")
    if running:
        return {"job_id": running[0][0]}
    if not (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .first()
    ):
        raise HTTPException(status_code=400, detail="还没有已成文的章节,无需扫描")
    if busy := _chapter_job_busy(project_id):
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await motifs_engine.scan_book_motifs(session, project_id, progress=progress)
        finally:
            session.close()

    return {"job_id": spawn_job(f"motifscan-{project_id}", work)}
