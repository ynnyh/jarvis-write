# app/api/polish.py
# -*- coding: utf-8 -*-
"""润色接口:整章 / 选段,锁情节改文笔 + 去 AI 味。

POST /api/projects/{id}/polish/chapter/{n}   润色整章(不落库,返回润色稿供预览)
POST /api/projects/{id}/polish/chapter/{n}/apply  把上一次润色稿写回定稿
POST /api/projects/{id}/chapters/{n}/polish-fragment  阅读时点选段落润色(带用户方向)
POST /api/polish/segment                     润色任意选段(前端选中文本直接传)
POST /api/polish/ai-flavor                   只做 AI 味检测(不调 LLM,秒回)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Outline
from app.db.session import get_db
from app.engines.polish import ai_flavor_report, polish_fragment, polish_text
from app.schemas.tendency import Tendency

router = APIRouter(tags=["polish"], dependencies=[Depends(get_current_user)])


class SegmentPolishRequest(BaseModel):
    text: str = Field(min_length=1)
    tendency: Tendency = Field(default_factory=dict)


class ChapterPolishRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)


class PolishResult(BaseModel):
    polished: str
    locked_facts: list[str]
    violations: list[dict]
    flavor_before: dict
    flavor_after: dict


class ApplyPolishRequest(BaseModel):
    polished_text: str = Field(min_length=1)


class FlavorRequest(BaseModel):
    text: str = Field(min_length=1)


def _chapter(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None or not ch.final_content:
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚无定稿正文")
    return ch


@router.post("/api/projects/{project_id}/polish/chapter/{n}", response_model=PolishResult)
async def polish_chapter(
    project_id: int, n: int, req: ChapterPolishRequest, db: Session = Depends(get_db)
):
    """润色整章(返回润色稿,不落库;满意后再调 /apply)。"""
    project = get_project_or_404(db, project_id)
    ch = _chapter(db, project_id, n)
    try:
        result = await polish_text(
            ch.final_content, req.tendency, project.global_tendency
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PolishResult(**result)


@router.post("/api/projects/{project_id}/polish/chapter/{n}/apply")
async def apply_chapter_polish(
    project_id: int, n: int, req: ApplyPolishRequest, db: Session = Depends(get_db)
):
    """把润色稿写回定稿(用户确认后)。"""
    get_project_or_404(db, project_id)
    ch = _chapter(db, project_id, n)
    # 覆盖前留一版:润色不满意可回退到润色前
    snapshot_chapter(db, ch, source="polished")
    ch.final_content = req.polished_text.strip()
    ch.word_count = len(ch.final_content)
    db.commit()
    return {"status": "applied", "chapter_number": n, "word_count": ch.word_count}


@router.post("/api/projects/{project_id}/polish/segment", response_model=PolishResult)
async def polish_segment_inget_project_or_404(
    project_id: int, req: SegmentPolishRequest, db: Session = Depends(get_db)
):
    """润色选段(带项目全局倾向)。"""
    project = get_project_or_404(db, project_id)
    try:
        result = await polish_text(req.text, req.tendency, project.global_tendency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PolishResult(**result)


@router.post("/api/polish/segment", response_model=PolishResult)
async def polish_segment(req: SegmentPolishRequest):
    """润色任意选段(无项目上下文)。"""
    try:
        result = await polish_text(req.text, req.tendency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PolishResult(**result)


# ---------- 阅读中片段润色(带润色方向) ----------

_MAX_FRAGMENT_CHARS = 2000


class FragmentPolishRequest(BaseModel):
    fragment: str = ""
    direction: str = Field(default="", max_length=200)


class FragmentPolishResult(BaseModel):
    polished: str
    notes: str | None = None


@router.post(
    "/api/projects/{project_id}/chapters/{n}/polish-fragment",
    response_model=FragmentPolishResult,
)
async def polish_chapter_fragment(
    project_id: int, n: int, req: FragmentPolishRequest, db: Session = Depends(get_db)
):
    """润色章节中的单个段落(阅读器点选):注入本章蓝图摘要作上下文,
    只改文笔不改情节,遵循用户润色方向。不落库,由前端确认后替换。"""
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚未生成")

    fragment = req.fragment.strip()
    if not fragment:
        raise HTTPException(status_code=400, detail="待润色片段不能为空")
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"片段最长 {_MAX_FRAGMENT_CHARS} 字,当前 {len(fragment)} 字",
        )

    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    try:
        result = await polish_fragment(
            fragment, req.direction, outline.summary if outline else ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FragmentPolishResult(**result)


@router.post("/api/polish/ai-flavor")
async def check_ai_flavor(req: FlavorRequest):
    """只做 AI 味检测(纯规则,不调 LLM,即时返回)。含分类得分与命中明细。"""
    return ai_flavor_report(req.text).to_dict()
