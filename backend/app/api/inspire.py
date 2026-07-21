# app/api/inspire.py
# -*- coding: utf-8 -*-
"""灵感接口:碎片/想法 → 结构化故事概念。独立于项目,可在建项目前用。

三条路(见 docs 灵感工坊设计):
  POST /api/inspire         出方案:碎片 → N 个差异化结构化概念
  POST /api/inspire/refine  指令式:当前概念 + 一句话修改 → 改后概念(带 diff)
  POST /api/inspire/chat    对话式:多轮聊 → 每轮沉淀出结构化概念草稿

概念结构统一走 app/schemas/concept.py(六字段,LLM 幻觉字段一律丢弃)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.engines.consistency.extractor import parse_llm_json
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.base import LLMAdapter, LLMMessage
from app.llm.router import Task, get_adapter_for
from app.prompts.inspire import (
    CHAT_DISTILL_PROMPT,
    CHAT_SYSTEM_PROMPT,
    INSPIRE_PROMPT,
    REFINE_PROMPT,
)
from app.schemas.concept import CONCEPT_FIELDS, Concept, coerce_concept
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.inspire")

router = APIRouter(
    prefix="/api/inspire",
    tags=["inspire"],
    dependencies=[Depends(get_current_user)],
)

# 对话轮数上限:防止 transcript 无限膨胀吃 token / 拖慢蒸馏
_MAX_CHAT_TURNS = 40
_MAX_MSG_LEN = 2000


# ============================= 出方案 =============================
class InspireRequest(BaseModel):
    spark: str = Field(default="", description="灵感碎片,可为空")
    tendency: Tendency = Field(default_factory=dict)
    count: int = Field(default=4, ge=2, le=6)


class InspireResponse(BaseModel):
    ideas: list[Concept]


@router.post("", response_model=InspireResponse)
async def inspire(req: InspireRequest) -> InspireResponse:
    """从灵感碎片扩展出 N 个结构化故事概念(强模型,约 1-2 分钟)。"""
    assembled = assemble_tendency("outline", req.tendency)
    prompt = INSPIRE_PROMPT.format(
        spark=req.spark.strip() or "(空白,自由发挥)",
        count=req.count,
        style_directives=render_style_block(assembled),
    )
    try:
        raw = await get_adapter_for(Task.ARCHITECTURE).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"灵感生成失败: {exc}") from exc

    data = parse_llm_json(raw)
    ideas = [
        coerce_concept(i)
        for i in (data.get("ideas") or [])
        if isinstance(i, dict)
    ]
    # 丢弃全空概念(解析残缺);全军覆没才报错
    ideas = [c for c in ideas if not c.is_empty()]
    if not ideas:
        raise HTTPException(status_code=502, detail="灵感解析失败,请重试")
    return InspireResponse(ideas=ideas)


# ============================= 指令式改 =============================
class RefineRequest(BaseModel):
    concept: Concept
    directive: str = Field(min_length=1, description="一句话修改意见")
    tendency: Tendency = Field(default_factory=dict)


class RefineResponse(BaseModel):
    concept: Concept
    changed: list[str] = Field(default_factory=list, description="实际改动的字段名")
    note: str = ""


@router.post("/refine", response_model=RefineResponse)
async def refine(req: RefineRequest) -> RefineResponse:
    """指令式局部改:据修改意见改写当前概念,前端做字段级 diff 预览后落库。"""
    directive = req.directive.strip()
    if not directive:
        raise HTTPException(status_code=400, detail="修改意见不能为空")
    if len(directive) > 500:
        raise HTTPException(status_code=400, detail="修改意见过长(限 500 字)")
    if req.concept.is_empty():
        raise HTTPException(status_code=400, detail="当前还没有概念可改,请先生成或填写")

    prompt = REFINE_PROMPT.format(
        concept_block=req.concept.render(),
        directive=directive,
    )
    try:
        raw = await get_adapter_for(Task.ARCHITECTURE).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"概念改写失败: {exc}") from exc

    data = parse_llm_json(raw)
    new_concept = coerce_concept(data.get("concept"))
    if new_concept.is_empty():
        raise HTTPException(status_code=502, detail="概念改写解析失败,请重试")

    # changed 以后端为准重算(不轻信模型自报),与原概念逐字段比对
    valid_fields = {k for k, _ in CONCEPT_FIELDS}
    changed = [
        k for k, _ in CONCEPT_FIELDS
        if getattr(new_concept, k).strip() != getattr(req.concept, k).strip()
    ]
    # 模型自报的 changed 仅作补充(它可能语义上"改了"但文字近似),取并集且过滤非法字段
    for k in data.get("changed") or []:
        if isinstance(k, str) and k in valid_fields and k not in changed:
            changed.append(k)

    return RefineResponse(
        concept=new_concept,
        changed=changed,
        note=str(data.get("note") or "").strip(),
    )


# ============================= 对话式捏 =============================
class ChatMessage(BaseModel):
    role: str = Field(description="user / assistant")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    concept: Concept | None = None
    tendency: Tendency = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    concept: Concept


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """对话式构思:回一句引导,并把整段对话蒸馏成结构化概念草稿。

    两次 LLM 调用:①按对话历史续聊 ②独立蒸馏成概念(不污染对话上下文)。
    """
    # 归一化 + 防御:限轮数、限单条长度、只留合法角色
    turns = [
        m for m in req.messages
        if m.role in ("user", "assistant") and m.content.strip()
    ][-_MAX_CHAT_TURNS:]
    if not turns:
        raise HTTPException(status_code=400, detail="请先说点什么")
    if turns[-1].role != "user":
        raise HTTPException(status_code=400, detail="最后一条应为用户发言")

    assembled = assemble_tendency("outline", req.tendency)
    style_block = render_style_block(assembled)
    current = req.concept or Concept()
    adapter = get_adapter_for(Task.ARCHITECTURE)

    # ① 续聊:system + 对话历史
    system = CHAT_SYSTEM_PROMPT.format(
        style_directives=style_block,
        concept_block=current.render() or "(还没有,刚开始聊)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m.role, content=m.content.strip()[:_MAX_MSG_LEN])
        for m in turns
    ]
    try:
        reply = (await _complete_text(adapter, chat_messages)).strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"对话失败: {exc}") from exc
    if not reply:
        raise HTTPException(status_code=502, detail="模型没有回应,请重试")

    # ② 蒸馏:把含最新 AI 回复的完整对话提炼成概念(独立调用)
    transcript = _format_transcript(turns, reply)
    distilled = current  # 蒸馏失败时回落到既有概念,不丢用户已捏出的东西
    try:
        raw = await adapter.ask(CHAT_DISTILL_PROMPT.format(transcript=transcript))
        candidate = coerce_concept(parse_llm_json(raw))
        if not candidate.is_empty():
            distilled = candidate
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("对话概念蒸馏失败,沿用既有概念", exc_info=True)

    return ChatResponse(reply=reply, concept=distilled)


async def _complete_text(adapter: LLMAdapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:带空回复重试 + 用量记账(对齐 ask 的兜底)。"""
    original_max = adapter.max_tokens
    try:
        for _ in range(3):
            resp = await adapter.complete(messages)
            adapter._record_usage(resp)
            content = (resp.content or "").strip()
            if content:
                return content
            adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
        raise RuntimeError("模型连续 3 次返回空回复")
    finally:
        adapter.max_tokens = original_max


def _format_transcript(turns: list[ChatMessage], latest_reply: str) -> str:
    lines = [
        f"{'作者' if m.role == 'user' else '策划'}:{m.content.strip()}"
        for m in turns
    ]
    lines.append(f"策划:{latest_reply}")
    return "\n".join(lines)
