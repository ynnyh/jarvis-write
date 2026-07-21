# app/api/edit_directive.py
# -*- coding: utf-8 -*-
"""修改指令接口:自然语言结构性修改 → LLM 改写受影响章大纲 → 预览 → 用户确认应用。

POST /api/projects/{id}/edit-directive        解析指令,返回改写预览(不落库)
POST /api/projects/{id}/edit-directive/apply  应用用户确认的改写(版本化落库 + 正文失配标记)

第一版范围:只动蓝图层;架构层不改写、不自动级联、实体退场只建议不执行。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Outline
from app.db.session import get_db
from app.engines.cascade import apply_outline_revision
from app.engines.common import chapter_architecture_brief, get_outline
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import EDIT_DIRECTIVE_PROMPT

logger = logging.getLogger("jarvis-write.edit-directive")

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["edit-directive"],
    dependencies=[Depends(get_current_user)],
)

# 蓝图注入 prompt 的截断控制:单章简述 / 全部蓝图总长度
_SUMMARY_SNIPPET = 120
_DIGEST_MAX = 12000


class DirectiveRequest(BaseModel):
    directive: str = Field(description="自然语言结构性修改指令")


class DirectiveItem(BaseModel):
    chapter_number: int
    new_title: str | None = None
    new_summary: str
    change_reason: str = ""


class DirectivePreview(BaseModel):
    analysis: str
    items: list[DirectiveItem]
    suggest_retire: list[str] = []


class ApplyItem(BaseModel):
    chapter_number: int
    new_title: str | None = None
    new_summary: str = Field(min_length=1)


class ApplyRequest(BaseModel):
    items: list[ApplyItem] = Field(min_length=1)


class ApplyResult(BaseModel):
    updated: list[int]
    stale_chapters: list[int]


def _blueprint_digest(outlines: list[Outline]) -> str:
    """全部章蓝图的紧凑文本(章号/标题/简述),超长截断控制 token。"""
    lines = [
        f"第{o.chapter_number}章《{o.title}》:{(o.summary or '')[:_SUMMARY_SNIPPET]}"
        for o in outlines
    ]
    digest = "\n".join(lines)
    if len(digest) > _DIGEST_MAX:
        digest = digest[:_DIGEST_MAX] + "\n……(后续章节从略)"
    return digest


@router.post("/edit-directive", response_model=DirectivePreview)
async def parse_directive(
    project_id: int, req: DirectiveRequest, db: Session = Depends(get_db)
):
    """解析修改指令:LLM 判断受影响章节并给出大纲改写预览。不落库。"""
    directive = req.directive.strip()
    if not directive:
        raise HTTPException(status_code=400, detail="修改指令不能为空")
    if len(directive) > 500:
        raise HTTPException(status_code=400, detail="修改指令过长(限500字)")

    project = get_project_or_404(db, project_id)
    outlines = (
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
        .all()
    )
    if not outlines:
        raise HTTPException(status_code=400, detail="还没有章节蓝图,请先生成蓝图")

    prompt = EDIT_DIRECTIVE_PROMPT.format(
        directive=directive,
        architecture_brief=chapter_architecture_brief(project),
        blueprint_digest=_blueprint_digest(outlines),
    )
    try:
        raw = await get_adapter_for(Task.IMPACT).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"指令解析失败: {exc}") from exc

    data = parse_llm_json(raw)
    if not data:
        raise HTTPException(status_code=502, detail="指令解析失败(模型输出无法解析),请重试")

    valid_numbers = {o.chapter_number for o in outlines}
    items = []
    for i in data.get("items") or []:
        if not isinstance(i, dict):
            continue
        try:
            n = int(i.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        new_summary = str(i.get("new_summary") or "").strip()
        if n not in valid_numbers or not new_summary:
            continue  # 幻觉章号/缺新简述的条目丢弃
        new_title = str(i.get("new_title") or "").strip() or None
        items.append(
            DirectiveItem(
                chapter_number=n,
                new_title=new_title,
                new_summary=new_summary,
                change_reason=str(i.get("change_reason") or "").strip(),
            )
        )

    suggest_retire = list(
        dict.fromkeys(
            s.strip() for s in (data.get("suggest_retire") or [])
            if isinstance(s, str) and s.strip()
        )
    )
    return DirectivePreview(
        analysis=str(data.get("analysis") or "").strip(),
        items=items,
        suggest_retire=suggest_retire,
    )


@router.post("/edit-directive/apply", response_model=ApplyResult)
async def apply_directive(
    project_id: int, req: ApplyRequest, db: Session = Depends(get_db)
):
    """应用用户确认的改写:版本化落库(复用级联引擎)+ 有正文的章标 is_stale。

    绕过 editOutline 的大改分级/影响分析:指令场景本就是批量结构改,
    change_summary 统一记"修改指令"。不自动级联,重生成交给用户决定。
    """
    get_project_or_404(db, project_id)
    updated: list[int] = []
    stale_chapters: list[int] = []
    for item in req.items:
        outline = get_outline(db, project_id, item.chapter_number)
        if outline is None:
            continue  # 预览后大纲被删等边界,跳过
        updates: dict = {"summary": item.new_summary}
        if item.new_title is not None:
            updates["title"] = item.new_title
        result = apply_outline_revision(
            db, outline, updates, change_summary="修改指令"
        )
        if result["status"] == "saved":
            updated.append(item.chapter_number)
            if result["own_chapter_stale"]:
                stale_chapters.append(item.chapter_number)
    db.commit()
    logger.info("修改指令应用: 项目%d 更新%s 失配%s", project_id, updated, stale_chapters)
    return ApplyResult(updated=updated, stale_chapters=stale_chapters)
