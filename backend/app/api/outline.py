# app/api/outline.py
# -*- coding: utf-8 -*-
"""大纲编辑与级联接口(核心差异化能力)。

PUT  /api/projects/{id}/outlines/{n}          编辑大纲 → diff 分级 → 版本快照
POST /api/projects/{id}/outlines/{n}/impact   下游影响分析(只分析不执行)
POST /api/projects/{id}/outlines/cascade      用户确认后级联重生成勾选的章节
GET  /api/projects/{id}/outlines/{n}/versions 版本历史
POST /api/projects/{id}/outlines/{n}/discuss  单章大纲 AI 研讨(多轮对话 → 改写提案)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, Outline, OutlineVersion, Project
from app.db.session import SessionLocal, get_db
from app.engines.cascade import (
    analyze_impact,
    apply_outline_edit,
    cascade_regenerate,
)
from app.engines.common import chapter_architecture_brief
from app.engines.outline_discuss import discuss_outline
from app.engines.outline_retitle import suggest_all_chapter_titles, suggest_chapter_titles
from app.engines.title_style import resolve_title_directive
from app.jobs import list_running, spawn_job
from app.schemas.project import OutlineOut
from app.schemas.tendency import Tendency

router = APIRouter(
    prefix="/api/projects/{project_id}/outlines",
    tags=["outline"],
    dependencies=[Depends(get_current_user)],
)


class OutlineUpdate(BaseModel):
    """所有字段可选,只传要改的。"""

    title: str | None = None
    chapter_role: str | None = None
    chapter_purpose: str | None = None
    suspense_level: str | None = None
    foreshadowing: str | None = None
    plot_twist_level: str | None = None
    summary: str | None = None
    characters_involved: list[Any] | None = None
    key_items: list[Any] | None = None
    scene_location: str | None = None
    beats: list[str] | None = None


class EditResult(BaseModel):
    status: str
    change_type: str | None
    change_summary: str
    changed_fields: list[str]
    own_chapter_stale: bool
    needs_impact_analysis: bool
    outline: OutlineOut


class ImpactItem(BaseModel):
    chapter_number: int
    reason: str
    action: str = "regenerate"


class ImpactReport(BaseModel):
    source_chapter: int
    affected: list[ImpactItem]
    overall: str


class CascadeRequest(BaseModel):
    source_chapter: int
    chapter_numbers: list[int] = Field(description="用户勾选要重生成的章节")
    reasons: dict[int, str] = Field(default_factory=dict)
    tendency: Tendency = Field(default_factory=dict)


class CascadeResult(BaseModel):
    updated: list[int]
    stale_chapters: list[int]
    warnings: list[str]
    outlines: list[OutlineOut]


class VersionOut(BaseModel):
    version: int
    change_type: str
    change_summary: str
    snapshot: dict

    model_config = {"from_attributes": True}


def _outline(db: Session, project_id: int, n: int) -> Outline:
    o = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    if o is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章大纲不存在")
    return o


@router.put("/{chapter_number}", response_model=EditResult)
async def edit_outline(
    project_id: int,
    chapter_number: int,
    req: OutlineUpdate,
    db: Session = Depends(get_db),
):
    """编辑大纲。major 改动会提示做影响分析(needs_impact_analysis=true)。"""
    get_project_or_404(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    result = await apply_outline_edit(
        db, outline, req.model_dump(exclude_none=True)
    )
    db.commit()
    return EditResult(
        **result, outline=OutlineOut.model_validate(outline, from_attributes=True)
    )


# ---------- 单章大纲 AI 研讨 ----------


class OutlineDiscussRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


class OutlineDiscussProposal(BaseModel):
    """蒸馏出的改写提案:与修改指令的 DirectiveItem 同构,前端走同一 apply 链路。"""

    new_title: str | None = None
    new_summary: str
    change_reason: str = ""


class OutlineDiscussResponse(BaseModel):
    reply: str
    proposal: OutlineDiscussProposal | None = None


def _outline_block(o: Outline) -> str:
    """本章大纲渲染成研讨上下文。"""
    lines = [
        f"标题:{o.title}",
        f"本章定位:{o.chapter_role}",
        f"核心作用:{o.chapter_purpose}",
        f"本章简述:{o.summary}",
        f"伏笔操作:{o.foreshadowing or '无'}",
        f"出场人物:{'、'.join(map(str, o.characters_involved or [])) or '—'}",
        f"场景地点:{o.scene_location or '—'}",
    ]
    beats = [str(b).strip() for b in (o.beats or []) if str(b).strip()]
    if beats:
        lines.append("场景节拍:" + ";".join(beats[:8]))
    return "\n".join(lines)


@router.post("/{chapter_number}/discuss", response_model=OutlineDiscussResponse)
async def discuss(
    project_id: int,
    chapter_number: int,
    req: OutlineDiscussRequest,
    db: Session = Depends(get_db),
):
    """就某一章的大纲与作者多轮研讨:聊清"哪里不对" → 蒸馏出改写提案。

    提案不落库:前端确认后调 edit-directive/apply(版本化落库 + 正文标失配)。
    """
    project = get_project_or_404(db, project_id)
    outline = _outline(db, project_id, chapter_number)

    neighbors = (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number.in_([chapter_number - 1, chapter_number + 1]),
        )
        .order_by(Outline.chapter_number)
        .all()
    )
    neighbor_block = "\n".join(
        f"第{o.chapter_number}章《{o.title}》:{(o.summary or '')[:120]}" for o in neighbors
    ) or "(无相邻章节)"

    written = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
            Chapter.final_content != "",
        )
        .first()
    )
    written_note = (
        f"第 {chapter_number} 章已有正文(改写大纲会把它标记为失配,需要重写)"
        if written else f"第 {chapter_number} 章尚未成文(改大纲代价低,可放开调)"
    )

    try:
        result = await discuss_outline(
            req.messages,
            chapter_number=chapter_number,
            architecture_brief=chapter_architecture_brief(project),
            outline_block=_outline_block(outline),
            neighbor_block=neighbor_block,
            written_note=written_note,
            current_summary=outline.summary or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OutlineDiscussResponse(**result)


# ---------- 章节标题润色(AI 换个更合适的标题)----------


class RetitleRequest(BaseModel):
    directive: str = ""


class RetitleResponse(BaseModel):
    titles: list[str]


@router.post("/{chapter_number}/retitle", response_model=RetitleResponse)
async def retitle(
    project_id: int,
    chapter_number: int,
    req: RetitleRequest,
    db: Session = Depends(get_db),
):
    """基于本章大纲生成若干候选标题(不落库)。作者选定后走 PUT 只改 title。"""
    project = get_project_or_404(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    try:
        titles = await suggest_chapter_titles(
            chapter_number=chapter_number,
            architecture_brief=chapter_architecture_brief(project),
            outline_block=_outline_block(outline),
            current_title=outline.title,
            directive=req.directive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RetitleResponse(titles=titles)


# ---------- 批量重拟标题(一键换一批,不动剧情)----------


class RetitleAllRequest(BaseModel):
    """directive: 作者自由文本要求;title_style: 预设 key(plain/hook/suspense/poetic)。
    chapter_numbers 不传=全书;传了只重拟这些章。两者一起由后端 resolve 成一句导向。"""

    directive: str = Field(default="", max_length=500)
    title_style: str = Field(default="", max_length=20)
    chapter_numbers: list[int] | None = None


class RetitleAllItem(BaseModel):
    chapter_number: int
    old_title: str
    new_title: str


class RetitleAllResponse(BaseModel):
    items: list[RetitleAllItem]


class ApplyRetitleItem(BaseModel):
    chapter_number: int
    new_title: str = Field(min_length=1, max_length=60)


class ApplyRetitleRequest(BaseModel):
    items: list[ApplyRetitleItem]


class ApplyRetitleResponse(BaseModel):
    updated: list[int]
    outlines: list[OutlineOut]


@router.post("/retitle-all", response_model=RetitleAllResponse)
async def retitle_all(
    project_id: int, req: RetitleAllRequest, db: Session = Depends(get_db)
):
    """为多章(默认全书)批量重拟标题,返回 old→new 供作者预览挑选。不落库。"""
    project = get_project_or_404(db, project_id)
    q = db.query(Outline).filter(Outline.project_id == project_id)
    if req.chapter_numbers:
        q = q.filter(Outline.chapter_number.in_(req.chapter_numbers))
    outlines = q.order_by(Outline.chapter_number).all()
    if not outlines:
        raise HTTPException(status_code=404, detail="还没有章节大纲")
    chapters = [
        {"chapter_number": o.chapter_number, "title": o.title, "summary": o.summary}
        for o in outlines
    ]
    try:
        items = await suggest_all_chapter_titles(
            architecture_brief=chapter_architecture_brief(project),
            chapters=chapters,
            directive=resolve_title_directive(req.title_style, req.directive),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RetitleAllResponse(items=[RetitleAllItem(**it) for it in items])


@router.post("/retitle-all/apply", response_model=ApplyRetitleResponse)
async def retitle_all_apply(
    project_id: int, req: ApplyRetitleRequest, db: Session = Depends(get_db)
):
    """把作者确认的一批新标题落库。逐章只改 title(cosmetic,不标正文失配)。"""
    get_project_or_404(db, project_id)
    if not req.items:
        raise HTTPException(status_code=400, detail="没有要应用的标题")
    updated: list[int] = []
    for it in req.items:
        outline = _outline(db, project_id, it.chapter_number)
        await apply_outline_edit(db, outline, {"title": it.new_title.strip()})
        updated.append(it.chapter_number)
    db.commit()
    outlines = (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number.in_(updated),
        )
        .order_by(Outline.chapter_number)
        .all()
    )
    return ApplyRetitleResponse(
        updated=updated,
        outlines=[OutlineOut.model_validate(o, from_attributes=True) for o in outlines],
    )


@router.post("/{chapter_number}/impact", response_model=ImpactReport)
async def impact(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """分析最新一次改动对下游的影响。只分析,不改任何数据。"""
    project = get_project_or_404(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    result = await analyze_impact(db, project, outline)
    return ImpactReport(
        source_chapter=result["source_chapter"],
        overall=result["overall"],
        affected=[ImpactItem(**a) for a in result["affected"]],
    )


@router.post("/{chapter_number}/impact-async")
async def impact_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """异步版影响分析:立即返回 job_id(分析 1-3 分钟)。"""
    get_project_or_404(db, project_id)
    _outline(db, project_id, chapter_number)  # 校验存在
    for jid, job in list_running(f"impact-{project_id}-"):
        if job["kind"] == f"impact-{project_id}-{chapter_number}":
            return {"job_id": jid}

    async def work(progress):
        progress(f"分析第 {chapter_number} 章改动的下游影响")
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            outline = (
                session.query(Outline)
                .filter(
                    Outline.project_id == project_id,
                    Outline.chapter_number == chapter_number,
                )
                .first()
            )
            result = await analyze_impact(session, project, outline)
            return {
                "source_chapter": result["source_chapter"],
                "overall": result["overall"],
                "affected": result["affected"],
            }
        finally:
            session.close()

    return {"job_id": spawn_job(f"impact-{project_id}-{chapter_number}", work)}


@router.post("/cascade", response_model=CascadeResult)
async def cascade(
    project_id: int, req: CascadeRequest, db: Session = Depends(get_db)
):
    """级联重生成用户勾选的章节大纲(用户拍板后才调用)。"""
    project = get_project_or_404(db, project_id)
    try:
        result = await cascade_regenerate(
            db,
            project,
            req.source_chapter,
            req.chapter_numbers,
            reasons=req.reasons,
            tendency=req.tendency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    outlines = (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number.in_(result["updated"]),
        )
        .order_by(Outline.chapter_number)
        .all()
    )
    return CascadeResult(
        **result,
        outlines=[OutlineOut.model_validate(o, from_attributes=True) for o in outlines],
    )


@router.post("/cascade-async")
async def cascade_async(
    project_id: int, req: CascadeRequest, db: Session = Depends(get_db)
):
    """异步版级联重生成:立即返回 job_id(可能重生成多章,数分钟)。"""
    get_project_or_404(db, project_id)
    for jid, job in list_running(f"cascade-{project_id}"):
        if job["kind"] == f"cascade-{project_id}":
            return {"job_id": jid}

    async def work(progress):
        progress(f"级联重生成 {len(req.chapter_numbers)} 章大纲")
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            result = await cascade_regenerate(
                session,
                project,
                req.source_chapter,
                req.chapter_numbers,
                reasons=req.reasons,
                tendency=req.tendency,
            )
            session.commit()
            outlines = (
                session.query(Outline)
                .filter(
                    Outline.project_id == project_id,
                    Outline.chapter_number.in_(result["updated"]),
                )
                .order_by(Outline.chapter_number)
                .all()
            )
            return {
                **result,
                "outlines": [
                    OutlineOut.model_validate(o, from_attributes=True).model_dump()
                    for o in outlines
                ],
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return {"job_id": spawn_job(f"cascade-{project_id}", work)}


@router.get("/{chapter_number}/versions", response_model=list[VersionOut])
async def versions(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    return list(
        db.query(OutlineVersion)
        .filter(OutlineVersion.outline_id == outline.id)
        .order_by(OutlineVersion.version)
    )
