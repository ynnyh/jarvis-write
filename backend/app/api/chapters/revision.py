# app/api/chapters/revision.py
# -*- coding: utf-8 -*-
"""章节改稿:重写研讨、多处批注定点改、手动编辑正文。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.api.sse import STREAM_HEADERS, sse_event
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Outline
from app.db.session import get_db
from app.engines.pipeline.chapter import (
    _rolling_summary,
    discuss_revision,
    discuss_revision_stream,
)
from app.engines.pipeline.continuation import continue_tail
from app.engines.polish import polish_fragment
from app.engines.tendency.assembler import voice_block_of
from app.jobs import list_running, spawn_job

from ._common import ChapterDetail

router = APIRouter()


class ReviseDiscussRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


class ReviseDiscussResponse(BaseModel):
    reply: str
    directive: str = ""
    # AI 档位建议(docs/10 §0 四级梯度):polish=③锁情节整章优化 / regenerate=④整章重生成;
    # None=模型没给建议(前端中性呈现两个选项,升档永远由用户确认)
    suggested_level: str | None = None


def _blueprint_block(outline: Outline | None, n: int) -> str:
    """把本章蓝图渲染成研讨对话的上下文;无蓝图时给提示。"""
    if outline is None:
        return f"(第 {n} 章还没有大纲蓝图)"
    return (
        f"第{n}章《{outline.title}》\n"
        f"- 核心作用:{outline.chapter_purpose}\n"
        f"- 伏笔操作:{outline.foreshadowing}\n"
        f"- 本章简述:{outline.summary}"
    )


@router.post("/{chapter_number}/revise-discuss", response_model=ReviseDiscussResponse)
async def revise_discuss(
    project_id: int,
    chapter_number: int,
    req: ReviseDiscussRequest,
    db: Session = Depends(get_db),
):
    """就某一章的重写与作者多轮研讨:聊清"到底哪里不满意" → 蒸馏出修改意见。

    前端拿返回的 directive 回填重写文本框,确认后作为 revision 去 generate-async 重写。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚无定稿正文,先生成再重写")
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    try:
        result = await discuss_revision(
            req.messages,
            blueprint_block=_blueprint_block(outline, chapter_number),
            chapter_block=ch.final_content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviseDiscussResponse(**result)


@router.post("/{chapter_number}/revise-discuss-stream")
async def revise_discuss_stream(
    project_id: int,
    chapter_number: int,
    req: ReviseDiscussRequest,
    db: Session = Depends(get_db),
):
    """revise-discuss 的真流式版(SSE 打字机):逐字吐 reply,收尾给 directive + 档位建议。

    校验/取数在请求上下文内完成(缺正文走正常 HTTP 404);进入 LLM 阶段后的错误
    (对话为空/上游失败)改走 SSE `error` 帧——此时 HTTP 头已 200 发出,无法再改状态码。
    与同步 revise-discuss 并存:前端优先走流式,老端点保留做回退/测试基线。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚无定稿正文,先生成再重写")
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    # 脱离请求上下文前把要用的都拍平成普通值(StreamingResponse 的生成器在响应体阶段才跑)
    blueprint = _blueprint_block(outline, chapter_number)
    chapter_block = ch.final_content
    messages = req.messages

    async def gen():
        try:
            async for kind, data in discuss_revision_stream(
                messages, blueprint_block=blueprint, chapter_block=chapter_block,
            ):
                if kind == "token":
                    yield sse_event("token", {"text": data})
                else:  # "done"
                    yield sse_event("done", data)
        except ValueError as exc:
            yield sse_event("error", {"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield sse_event("error", {"detail": f"对话失败: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=STREAM_HEADERS)


class AnnotationItem(BaseModel):
    para_idx: int = Field(ge=0)
    original: str = Field(min_length=1)
    note: str = Field(default="", max_length=200)


class ReviseAnnotatedRequest(BaseModel):
    annotations: list[AnnotationItem] = Field(min_length=1, max_length=30)


@router.post("/{chapter_number}/revise-annotated-async")
async def revise_annotated_async(
    project_id: int,
    chapter_number: int,
    req: ReviseAnnotatedRequest,
    db: Session = Depends(get_db),
):
    """②档「多处批注改」:一次性对若干被批注段落做定点改写(job 模式)。

    每条批注 = {para_idx, original(原文快照), note(意见)};逐条复用①档的
    polish_fragment(只改文笔不改情节),返回逐段 {old,new} 供前端做 diff 逐条验收。
    不落库——用户在前端逐条接受后走 PUT content 写回并留快照。

    单条校验失败(空/超长)只把该条标 ok=False,不拖垮整个 job;基础设施错误
    (LLM 网络/欠费)按 spawn_job 语义整个 job 失败,前端可整体重试(kind 去重)。
    """
    project = get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None or not (ch.final_content or "").strip():
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚无定稿正文")
    # 同章批注改任务已在跑 → 复用(去重复提交/断线重连)
    kind = f"revise-annotated-{project_id}-{chapter_number}"
    for jid, job in list_running(f"revise-annotated-{project_id}-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    summary = outline.summary if outline else ""
    # 文风范本(去 AI 味正向锚):请求线程内先算成字符串,脱 ORM 带进 job 协程
    voice_block = voice_block_of(project.global_tendency)
    # 脱离 ORM:job 协程跑在请求 session 之外(见 polish._cards 注释)
    items = [
        {"para_idx": a.para_idx, "original": a.original.strip(), "note": a.note}
        for a in req.annotations
    ]

    async def work(progress):
        pairs = []
        total = len(items)
        for i, it in enumerate(items, 1):
            progress(f"正在改第 {i}/{total} 处批注")
            try:
                r = await polish_fragment(
                    it["original"], it["note"], summary, voice_block=voice_block
                )
                pairs.append({
                    "para_idx": it["para_idx"], "old": it["original"],
                    "new": r["polished"], "notes": r.get("notes"), "ok": True,
                })
            except ValueError as exc:
                pairs.append({
                    "para_idx": it["para_idx"], "old": it["original"],
                    "new": "", "notes": str(exc), "ok": False,
                })
        return {"pairs": pairs}

    return {"job_id": spawn_job(kind, work)}


class EditContentRequest(BaseModel):
    final_content: str = Field(min_length=1)


@router.put("/{chapter_number}/content", response_model=ChapterDetail)
async def edit_content(
    project_id: int,
    chapter_number: int,
    req: EditContentRequest,
    db: Session = Depends(get_db),
):
    """手动编辑正文:立即保存。保存后请调 re-extract-async 同步圣经/摘要。"""
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")
    # 覆盖前留一版:手改后悔可回退到编辑前
    snapshot_chapter(db, ch, source="edited")
    ch.final_content = req.final_content.strip()
    ch.word_count = len(ch.final_content)
    # 手改后内容未经审校/人工审核,回到待审核(docs/08 §5.5)
    ch.status = "pending_review"
    db.commit()
    return ch


# ---------- 章尾续写(ghost text:顺着已写正文续一小段,Tab 接受后追加) ----------


class ContinueRequest(BaseModel):
    note: str = Field(default="", max_length=200)  # 作者额外要求(可空)


class ContinueResult(BaseModel):
    continuation: str


@router.post("/{chapter_number}/continue", response_model=ContinueResult)
async def continue_chapter(
    project_id: int,
    chapter_number: int,
    req: ContinueRequest,
    db: Session = Depends(get_db),
):
    """章尾续写:基于本章蓝图 + 前情 + 已写正文尾部,续写一个自然段(不落库)。

    前端把返回的 continuation 以灰字 ghost 显示在正文末尾,作者 Tab 接受后作为新段落追加
    (写回走既有 PUT /content 链路)。轻量单次调用,不做事实抽取/校验。
    """
    project = get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")

    text = (ch.final_content or ch.draft_content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="本章还没有正文,先写或生成一段再续写")

    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    try:
        continuation = await continue_tail(
            outline.summary if outline else "",
            _rolling_summary(db, project_id, chapter_number),
            text,
            req.note,
            voice_block=voice_block_of(project.global_tendency),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ContinueResult(continuation=continuation)
