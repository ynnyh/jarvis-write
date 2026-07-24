# app/api/editorial.py
# -*- coding: utf-8 -*-
"""编辑部接口:主编评分 / 校对 / 审核报告 / 优化动作目录。

GET  /api/editorial/actions                          预设优化动作(正文/大纲两级,配置文件驱动)
POST /api/projects/{id}/chapters/{n}/review-async    主编评分(四维+短评+3条建议)
POST /api/projects/{id}/chapters/{n}/proofread-async 校对(错别字/语病/标点/重复,问题清单)
POST /api/projects/{id}/chapters/{n}/proofread-apply 应用勾选的校对修复(逐条精确替换)
GET  /api/projects/{id}/audit-report                 审核报告(聚合失配章/伏笔/退场人物,零 LLM)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Foreshadowing, Outline
from app.db.session import SessionLocal, get_db
from app.engines.editorial import (
    apply_proofread_fixes,
    judge_passed,
    load_proofread_snapshot,
    load_review_snapshot,
    proofread_chapter,
    review_chapter,
    store_proofread_snapshot,
    store_review_snapshot,
)
from app.jobs import list_running, spawn_job

router = APIRouter(tags=["editorial"], dependencies=[Depends(get_current_user)])

_ACTIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "editor_actions.json"


@lru_cache
def _actions() -> dict:
    with open(_ACTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/editorial/actions")
async def editorial_actions() -> dict:
    """预设优化动作目录(前端渲染 chips;prose=正文级,outline=大纲级)。"""
    return _actions()


def _chapter_with_content(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚无定稿正文")
    return ch


# ---------- 主编评分 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/review-async")
async def review_async(project_id: int, n: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"review-{project_id}-"):
        if job["kind"] == f"review-{project_id}-{n}":
            return {"job_id": jid}
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    outline_block = (
        f"标题:{outline.title}\n目的:{outline.chapter_purpose}\n概要:{outline.summary}"
        if outline else "(无蓝图)"
    )
    content = ch.final_content
    threshold = project.review_pass_threshold

    async def work(progress):
        progress(f"主编正在审读第 {n} 章")
        result = await review_chapter(content, outline_block)
        result["chapter_number"] = n
        # 达标与否由后端按项目阈值硬判,不靠模型自报
        result["passed"] = judge_passed(result["scores"], threshold)
        result["threshold"] = threshold
        # 打上来源/时间标记,与回显快照的展示口径一致(前端据此显示「手动审校」)
        result["source"] = "manual"
        result["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        # 结果落库:编辑部下次打开直接回显,不必再点一次「请主编审读」
        _persist_review_snapshot(project_id, n, result, content)
        return result

    return {"job_id": spawn_job(f"review-{project_id}-{n}", work)}


def _persist_review_snapshot(project_id: int, n: int, result: dict, content: str) -> None:
    """手动主审完成后把结果存进章节快照(独立会话;后台任务里请求会话已关闭)。

    content 是本次审读所依据的正文,作为指纹——若审完正文已被改动,回显自动失效。
    落库失败不影响主审结果正常返回给用户。
    """
    session = SessionLocal()
    try:
        ch = (
            session.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch is not None:
            store_review_snapshot(ch, result, "manual", content)
            session.commit()
    except Exception:  # noqa: BLE001 — 快照落库失败不阻塞主审结果
        session.rollback()
    finally:
        session.close()


@router.get("/api/projects/{project_id}/chapters/{n}/review")
async def get_review(project_id: int, n: int, db: Session = Depends(get_db)):
    """回显最近一次主审结果(生成时或手动),前端进编辑部直接展示。

    正文被编辑/润色/重写/回滚后指纹对不上 → review 为 null(不显示过期评分),
    用户可点「请主编审读」重新审。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章不存在")
    return {"review": load_review_snapshot(ch)}


# ---------- 校对 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/proofread-async")
async def proofread_async(project_id: int, n: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"proofread-{project_id}-"):
        if job["kind"] == f"proofread-{project_id}-{n}":
            return {"job_id": jid}
    content = ch.final_content

    async def work(progress):
        progress(f"校对正在逐句检查第 {n} 章")
        result = await proofread_chapter(content)
        result["chapter_number"] = n
        # 待修清单落库:编辑部下次打开直接回显,正文没变就不必再跑一次校对
        _persist_proofread_snapshot(project_id, n, result["issues"], content)
        return result

    return {"job_id": spawn_job(f"proofread-{project_id}-{n}", work)}


def _persist_proofread_snapshot(
    project_id: int, n: int, issues: list[dict], content: str
) -> None:
    """手动校对完成后把待修清单存进章节快照(独立会话;后台任务里请求会话已关闭)。

    content 是本次校对所依据的正文,作为指纹——用户应用修复后正文变动、指纹失配,
    过期清单自动不再回显。落库失败不影响校对结果正常返回给用户。
    """
    session = SessionLocal()
    try:
        ch = (
            session.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch is not None:
            store_proofread_snapshot(ch, issues, "manual", content, fixed=0)
            session.commit()
    except Exception:  # noqa: BLE001 — 快照落库失败不阻塞校对结果
        session.rollback()
    finally:
        session.close()


@router.get("/api/projects/{project_id}/chapters/{n}/proofread")
async def get_proofread(project_id: int, n: int, db: Session = Depends(get_db)):
    """回显最近一次校对结果(生成时自动修复的 / 手动待修的),前端进编辑部直接展示。

    正文被编辑/润色/重写/回滚后指纹对不上 → proofread 为 null(不显示过期清单),
    用户可点「开始校对」重新跑。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章不存在")
    return {"proofread": load_proofread_snapshot(ch)}


class ProofreadApplyRequest(BaseModel):
    fixes: list[dict] = Field(min_length=1, max_length=20, description="[{original, suggestion}]")


@router.post("/api/projects/{project_id}/chapters/{n}/proofread-apply")
async def proofread_apply(
    project_id: int, n: int, req: ProofreadApplyRequest, db: Session = Depends(get_db)
):
    """应用勾选的校对修复:逐条精确替换首次出现;改前留版本快照。

    返回 applied/failed 清单;正文有实质变化时建议前端随后调 re-extract-async。
    """
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    content, applied, failed = apply_proofread_fixes(ch.final_content, req.fixes)
    if applied:
        snapshot_chapter(db, ch, source="edited")
        ch.final_content = content
        ch.word_count = len(content)
        db.commit()
    return {
        "applied": applied,
        "failed": failed,
        "word_count": ch.word_count,
        "final_content": ch.final_content,
    }


# ---------- 审核报告(零 LLM,聚合现有数据) ----------

@router.get("/api/projects/{project_id}/audit-report")
async def audit_report(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    written = {c.chapter_number for c in chapters}
    max_written = max(written) if written else 0
    stale = [c.chapter_number for c in chapters if c.is_stale]

    fores = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .order_by(Foreshadowing.chapter_planted)
        .all()
    )
    # 逾期:预期回收章已写过但状态仍未回收
    overdue = [
        {
            "description": f.description,
            "planted": f.chapter_planted,
            "expected": f.expected_payoff_chapter,
            "status": f.status,
        }
        for f in fores
        if f.status in ("planted", "reinforced")
        and f.expected_payoff_chapter is not None
        and f.expected_payoff_chapter <= max_written
    ]
    open_count = sum(1 for f in fores if f.status in ("planted", "reinforced"))
    resolved_count = sum(1 for f in fores if f.status == "paid_off")

    # 大纲已生成但长期没写的章(跳章检查:前面留洞)
    outline_nums = [
        o.chapter_number
        for o in db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    ]
    holes = [
        num for num in outline_nums if num < max_written and num not in written
    ]

    return {
        "written_chapters": len(chapters),
        "target_chapters": project.target_chapters,
        "stale_chapters": stale,
        "holes": holes,
        "foreshadow": {
            "total": len(fores),
            "open": open_count,
            "resolved": resolved_count,
            "overdue": overdue,
        },
    }
