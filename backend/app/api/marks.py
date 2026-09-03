# app/api/marks.py
# -*- coding: utf-8 -*-
"""跨章标记接口:作者在正文里随手记的「这里不行」,落库持久,攒够全书批修。

GET    /api/projects/{id}/marks                  全书 open 标记(按章/段序)
POST   /api/projects/{id}/marks                  记一条标记(同段同快照 = 改意见,幂等)
DELETE /api/projects/{id}/marks/{mark_id}        移除/销账一条标记
POST   /api/projects/{id}/marks/revise-async     全书批修:一句总描述驱动逐标记锁情节改写(job)

批修只产出待验收替换对、不落库;前端逐条 diff 验收后走既有 PUT content 写回
(自动留版本快照),接受即 DELETE 对应标记销账。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, ChapterMark
from app.db.session import SessionLocal, get_db
from app.jobs import list_running, spawn_job

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["marks"],
    dependencies=[Depends(get_current_user)],
)


class MarkOut(BaseModel):
    id: int
    chapter_number: int
    para_idx: int
    snapshot: str
    note: str


class MarkCreateRequest(BaseModel):
    chapter_number: int = Field(ge=1)
    para_idx: int = Field(ge=0)
    snapshot: str = Field(min_length=1, max_length=20000, description="段落原文快照(失效判定用)")
    note: str = Field(default="", max_length=200, description="一句话意见")


class MarksReviseRequest(BaseModel):
    directive: str = Field(min_length=2, max_length=1000, description="一句总描述,全书批修统一遵循")


def _load_chapter(db: Session, project_id: int, chapter_number: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None or not (ch.final_content or "").strip():
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚无定稿正文,先有正文才能记标记")
    return ch


@router.get("/marks", response_model=list[MarkOut])
async def list_marks(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    rows = (
        db.query(ChapterMark)
        .filter(ChapterMark.project_id == project_id, ChapterMark.status == "open")
        .order_by(ChapterMark.chapter_number, ChapterMark.para_idx, ChapterMark.id)
        .all()
    )
    return [
        MarkOut(id=r.id, chapter_number=r.chapter_number, para_idx=r.para_idx,
                snapshot=r.snapshot, note=r.note)
        for r in rows
    ]


@router.post("/marks", response_model=MarkOut)
async def create_mark(project_id: int, req: MarkCreateRequest, db: Session = Depends(get_db)):
    """记一条标记;同章同段同快照已存在 → 只更新意见(幂等,不堆重复行)。"""
    _load_chapter(db, project_id, req.chapter_number)
    existing = (
        db.query(ChapterMark)
        .filter(
            ChapterMark.project_id == project_id,
            ChapterMark.status == "open",
            ChapterMark.chapter_number == req.chapter_number,
            ChapterMark.para_idx == req.para_idx,
            ChapterMark.snapshot == req.snapshot.strip(),
        )
        .first()
    )
    if existing is not None:
        if req.note.strip():
            existing.note = req.note.strip()
            db.commit()
        return existing
    row = ChapterMark(
        project_id=project_id,
        chapter_number=req.chapter_number,
        para_idx=req.para_idx,
        snapshot=req.snapshot.strip(),
        note=req.note.strip(),
        status="open",
    )
    db.add(row)
    db.commit()
    return row


@router.delete("/marks/{mark_id}")
async def delete_mark(project_id: int, mark_id: int, db: Session = Depends(get_db)):
    """移除标记;验收接受一条批修结果后前端也调这里销账。"""
    get_project_or_404(db, project_id)
    row = (
        db.query(ChapterMark)
        .filter(ChapterMark.id == mark_id, ChapterMark.project_id == project_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="标记不存在或已销账")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/marks/revise-async")
async def marks_revise_async(
    project_id: int, req: MarksReviseRequest, db: Session = Depends(get_db)
):
    """全书批修:对全部 open 标记逐条做锁情节改写(一句总描述统一指挥)。

    job 只产出待验收替换对,不改正文;同项目批修任务已在跑 → 复用。
    读正文 + 调 LLM,与生成任务无写冲突,故不做章节级互斥(应用侧还有
    前端快照守卫兜底)。
    """
    get_project_or_404(db, project_id)
    kind = f"marks-revise-{project_id}"
    for jid, job in list_running(kind):
        if job["kind"] == kind:
            return {"job_id": jid}
    has_marks = (
        db.query(ChapterMark.id)
        .filter(ChapterMark.project_id == project_id, ChapterMark.status == "open")
        .first()
    )
    if has_marks is None:
        raise HTTPException(status_code=400, detail="还没有任何标记:先在正文里选中段落点「批注」记下要改的地方")

    async def work(progress) -> dict:
        from app.engines.marks import revise_marks

        session = SessionLocal()
        try:
            return await revise_marks(session, project_id, req.directive, progress=progress)
        finally:
            session.close()

    return {"job_id": spawn_job(kind, work)}
