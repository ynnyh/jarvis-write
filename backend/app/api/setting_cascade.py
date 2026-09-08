# app/api/setting_cascade.py
# -*- coding: utf-8 -*-
"""设定级级联接口:设定变更 → 全书影响扫描 → 段落定点修提案。

POST /api/projects/{id}/setting-cascade/scan-async   扫描(old/new 规则全文,后端 diff)
POST /api/projects/{id}/setting-cascade/patch-async  提案(入参 scan 结果,可删减)

纪律:两个端点都只产「预览/提案」,不落库不改正文——扫描结果由前端持有、
筛选后回传给 patch;改写对经前端逐条 diff 验收后走 paraEdit 写回,再由
既有 re-extract-async 同步圣经/摘要。与全书批修(marks)同一信任模型。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.session import SessionLocal, get_db
from app.engines.setting_cascade import diff_rules, patch_passages, scan_setting_impact
from app.jobs import list_running, spawn_job

router = APIRouter(
    tags=["setting-cascade"], dependencies=[Depends(get_current_user)]
)

_MAX_PASSAGES = 200  # 单批定点修上限(提案要人逐条验收,多了没有意义)


class ScanRequest(BaseModel):
    old_text: str = ""
    new_text: str = ""


class PatchPair(BaseModel):
    chapter_number: int
    para_idx: int


class PatchRequest(BaseModel):
    changes: list[dict] = Field(default_factory=list)
    passages: list[PatchPair]


def _cascade_busy(project_id: int) -> str:
    """设定级联与其他全书任务互斥(粗筛要逐章跑,撞上生成/体检会互相拖慢)。"""
    for prefix in (
        f"chapter-{project_id}-", f"re-extract-{project_id}-",
        f"rulescan-{project_id}", f"diag-{project_id}",
        f"setting-scan-{project_id}", f"setting-patch-{project_id}",
    ):
        if jobs := list_running(prefix):
            return jobs[0].get("stage") or "进行中"
    return ""


@router.post("/api/projects/{project_id}/setting-cascade/scan-async")
async def setting_scan_async(
    project_id: int, req: ScanRequest, db: Session = Depends(get_db)
):
    """扫描设定变更的影响:返回 job,结果含章级命中与段级定位。"""
    get_project_or_404(db, project_id)
    changes = diff_rules(req.old_text, req.new_text)
    if not changes:
        raise HTTPException(status_code=400, detail="新旧设定没有差异,无需级联。")
    if busy := _cascade_busy(project_id):
        raise HTTPException(
            status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。"
        )

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await scan_setting_impact(
                session, project_id, changes, progress=progress
            )
        finally:
            session.close()

    return {"job_id": spawn_job(f"setting-scan-{project_id}", work)}


@router.post("/api/projects/{project_id}/setting-cascade/patch-async")
async def setting_patch_async(
    project_id: int, req: PatchRequest, db: Session = Depends(get_db)
):
    """对选中的冲突段产出改写提案(不落库,前端逐条 diff 验收)。"""
    get_project_or_404(db, project_id)
    if not req.passages:
        raise HTTPException(status_code=400, detail="没有选中任何冲突段落。")
    if len(req.passages) > _MAX_PASSAGES:
        raise HTTPException(
            status_code=400,
            detail=f"单批最多 {_MAX_PASSAGES} 段,请分批处理。",
        )
    if not req.changes:
        raise HTTPException(status_code=400, detail="缺少设定变更内容。")
    if busy := _cascade_busy(project_id):
        raise HTTPException(
            status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。"
        )

    passages = [
        {"chapter_number": p.chapter_number, "para_idx": p.para_idx}
        for p in req.passages
    ]

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await patch_passages(
                session, project_id, req.changes, passages, progress=progress
            )
        finally:
            session.close()

    return {"job_id": spawn_job(f"setting-patch-{project_id}", work)}
