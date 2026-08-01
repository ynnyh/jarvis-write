# app/engines/outline_discuss.py
# -*- coding: utf-8 -*-
"""单章大纲研讨:与作者多轮对话聊清"这章大纲哪里不对" → 蒸馏成改写提案。

与正文重写研讨(discuss_revision)同构:① 带上下文续聊(架构简报 + 本章大纲
+ 相邻章 + 成文状态);② 独立调用把对话蒸馏成结构化提案
{new_title, new_summary, change_reason}(无明确方向时为 None)。
提案本身不落库——前端确认后走修改指令的 apply 链路(版本化 + 标失配)。
"""
from __future__ import annotations

import logging

from app.engines.consistency.extractor import parse_llm_json
from app.engines.pipeline.chapter import (
    _MAX_REVISE_CHAT_TURNS,
    _MAX_REVISE_MSG_LEN,
    _format_revise_transcript,
    _revise_complete,
)
from app.llm.router import Task, get_adapter_for
from app.llm.base import LLMMessage
from app.prompts.cascade import (
    OUTLINE_DISCUSS_DISTILL_PROMPT,
    OUTLINE_DISCUSS_SYSTEM_PROMPT,
)

logger = logging.getLogger("jarvis-write.outline-discuss")


async def discuss_outline(
    messages: list[dict],
    *,
    chapter_number: int,
    architecture_brief: str,
    outline_block: str,
    neighbor_block: str,
    written_note: str,
    current_summary: str,
) -> dict:
    """多轮研讨某一章的大纲改写。返回 {reply, proposal};proposal 可为 None。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - 上下文块由调用方(API 层)按本章/相邻章/成文状态渲染好传入。
    """
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_REVISE_CHAT_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    adapter = get_adapter_for(Task.BLUEPRINT)

    # ① 续聊:system(带大纲上下文)+ 对话历史
    system = OUTLINE_DISCUSS_SYSTEM_PROMPT.format(
        chapter_number=chapter_number,
        architecture_brief=architecture_brief,
        outline_block=outline_block,
        neighbor_block=neighbor_block,
        written_note=written_note,
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_REVISE_MSG_LEN])
        for m in turns
    ]
    reply = (await _revise_complete(adapter, chat_messages)).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")

    # ② 蒸馏:把含最新回复的完整对话提炼成大纲改写提案(独立调用,不污染对话)
    transcript = _format_revise_transcript(turns, reply)
    proposal = None
    try:
        raw = (await adapter.ask(
            OUTLINE_DISCUSS_DISTILL_PROMPT.format(
                chapter_number=chapter_number,
                current_summary=current_summary or "(空)",
                transcript=transcript,
            )
        )).strip()
        if raw and raw != "-":
            data = parse_llm_json(raw)
            new_summary = str(data.get("new_summary") or "").strip()
            if new_summary:
                new_title = str(data.get("new_title") or "").strip() or None
                proposal = {
                    "new_title": new_title,
                    "new_summary": new_summary[:200],
                    "change_reason": str(data.get("change_reason") or "").strip(),
                }
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("大纲研讨蒸馏失败,proposal 置空", exc_info=True)

    return {"reply": reply, "proposal": proposal}
