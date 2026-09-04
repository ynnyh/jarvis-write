# app/engines/pipeline/rewrite_session.py
# -*- coding: utf-8 -*-
"""重写研讨(对话式:聊清不满意 → 蒸馏成重写要求)。

与架构研讨(discuss_architecture)同构的「续聊 + 独立蒸馏」两段式,只是上下文
从整本书架构换成单章蓝图+正文。蒸馏出的 directive 回填进重写文本框,作为
generate_chapter 的 revision 参数走既有 _revision_block 注入草稿,管线零改动。

自 chapter.py 拆出:本模块是独立的对话式重写入口(不进生成主流程),
chapter.py 仅保留兼容性再导出。
"""
from __future__ import annotations

import logging

from app.engines.consistency.extractor import parse_llm_json
from app.llm.base import LLMMessage, complete_text_with_budget
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import REVISE_CHAT_SYSTEM_PROMPT, REVISE_DISTILL_PROMPT

logger = logging.getLogger("jarvis-write.chapter")

_MAX_REVISE_CHAT_TURNS = 40
_MAX_REVISE_MSG_LEN = 2000
_MAX_REVISE_CHAPTER_CHARS = 3000  # 当前正文注入 system 时截断,防 token 膨胀


async def _revise_complete(adapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:空正文放大预算重试 + 用量记账。

    别在这里自己写"空串就翻倍"的循环:complete() 遇空正文是抛 EmptyContentError,
    统一交给 complete_text_with_budget 处理(见 llm/base.py)。
    """
    return await complete_text_with_budget(adapter, messages)


def _format_revise_transcript(turns: list[dict], latest_reply: str) -> str:
    lines = [
        f"{'作者' if m['role'] == 'user' else '编辑'}:{(m['content'] or '').strip()}"
        for m in turns
    ]
    lines.append(f"编辑:{latest_reply}")
    return "\n".join(lines)


async def discuss_revision(
    messages: list[dict],
    *,
    blueprint_block: str,
    chapter_block: str,
) -> dict:
    """就某一章的重写与作者多轮研讨:聊清"到底哪里不满意",蒸馏出重写要求。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - blueprint_block/chapter_block:本章蓝图与当前正文节选,供编辑理解上下文。

    返回 {reply, directive};directive 为蒸馏出的修改意见(可为空串),前端回填进
    重写文本框,确认后作为 revision 参数去重写本章。
    """
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_REVISE_CHAT_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    adapter = get_adapter_for(Task.DRAFT)

    # ① 续聊:system(带蓝图+正文上下文)+ 对话历史
    system = REVISE_CHAT_SYSTEM_PROMPT.format(
        blueprint_block=blueprint_block,
        chapter_block=chapter_block[:_MAX_REVISE_CHAPTER_CHARS] or "(本章还没有正文)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_REVISE_MSG_LEN])
        for m in turns
    ]
    reply = (await _revise_complete(adapter, chat_messages)).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")

    # ② 蒸馏:把含最新回复的完整对话提炼成「修改意见 + 档位建议」(独立调用,不污染对话)
    directive, level = await _distill_revision(adapter, turns, reply)
    return {"reply": reply, "directive": directive, "suggested_level": level}


async def _distill_revision(adapter, turns: list[dict], reply: str) -> tuple[str, str | None]:
    """把含最新回复的完整对话蒸馏成「修改意见 directive + 档位建议 level」。

    独立 ask 调用(不污染对话);蒸馏出"尚无明确意见"时约定回空/短横线,归一化成空串。
    失败不抛(蒸馏不该阻塞对话本身),返回 ("", None) 让前端中性呈现两个档位选项。
    同步 discuss_revision 与流式 discuss_revision_stream 共用这一份,行为一致。
    """
    transcript = _format_revise_transcript(turns, reply)
    try:
        raw = (await adapter.ask(REVISE_DISTILL_PROMPT.format(transcript=transcript))).strip()
        if raw and raw != "-":
            parsed = parse_llm_json(raw)
            if isinstance(parsed.get("directive"), str):
                # JSON 契约:directive 正文 + level 档位建议(polish=锁情节优化 / regenerate=重生成)
                lv = parsed.get("level")
                return parsed["directive"].strip(), (lv if lv in ("polish", "regenerate") else None)
            # 模型没按 JSON 输出:整段当意见,不给档位建议
            return raw, None
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("重写研讨蒸馏失败,directive 置空", exc_info=True)
    return "", None


async def discuss_revision_stream(
    messages: list[dict],
    *,
    blueprint_block: str,
    chapter_block: str,
):
    """流式版 discuss_revision(SSE 打字机):逐字产出 reply,收尾给 directive + 档位建议。

    产出 (kind, payload):
      ("token", str)  reply 的增量文字
      ("done", {"reply": str, "directive": str, "suggested_level": str | None})
    校验/system 构造同 discuss_revision(同步孪生);reply 流式吐完后再做一次(非流式)蒸馏。
    流式路径不重试空回复、不记账(与 openai_compatible._complete_via_stream 的取舍一致)。
    """
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_REVISE_CHAT_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    adapter = get_adapter_for(Task.DRAFT)
    system = REVISE_CHAT_SYSTEM_PROMPT.format(
        blueprint_block=blueprint_block,
        chapter_block=chapter_block[:_MAX_REVISE_CHAPTER_CHARS] or "(本章还没有正文)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_REVISE_MSG_LEN])
        for m in turns
    ]
    chunks: list[str] = []
    async for delta in adapter.stream(chat_messages):
        if not delta:
            continue
        chunks.append(delta)
        yield ("token", delta)
    reply = "".join(chunks).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")
    directive, level = await _distill_revision(adapter, turns, reply)
    yield ("done", {"reply": reply, "directive": directive, "suggested_level": level})
