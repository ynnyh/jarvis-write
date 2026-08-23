# app/engines/promo/chat.py
# -*- coding: utf-8 -*-
"""研讨对话(多轮流式):宣传片策划总监与用户聊方向,聊透再动手。

同润色研讨的流式模式(SSE 打字机):逐字产出 reply,末尾 done 帧收尾。
对话记录由 API 层持久化进 plan.chat_log;这里只负责把当前轮聊好。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.db.models import PromoPlan
from app.engines.promo.common import angles_block, direction_block
from app.llm.base import LLMMessage
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_CHAT_SYSTEM

# 上下文窗口保护:最多带最近 N 轮
_MAX_TURNS = 20
_MAX_MSG_LEN = 4000


class PromoChatError(ValueError):
    """研讨对话的业务性错误(信息直接上屏)。"""


def build_chat_messages(plan: PromoPlan, messages: list[dict]) -> list[LLMMessage]:
    """校验用户侧消息并组装 system + 多轮上下文。"""
    turns = [
        m
        for m in (messages or [])
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and str(m.get("content") or m.get("text") or "").strip()
    ][-_MAX_TURNS:]
    if not turns:
        raise PromoChatError("请先说点什么")
    if turns[-1].get("role") != "user":
        raise PromoChatError("最后一条应为你的发言")

    material = (plan.material_notes or "").strip()
    system = PROMO_CHAT_SYSTEM.format(
        duration_s=plan.duration_s,
        subject=plan.subject.strip() or "(未定,先问客户)",
        angles_block=angles_block(plan.angles),
        direction_block=direction_block(plan.direction or "live"),
        material_block=material or "(客户还没给素材点——提醒他补:史实/数据/slogan 只能用他给的)",
        brief_block=(
            f"【已收敛的简报(研讨围绕它修订)】\n{str(plan.brief)[:1500]}"
            if (plan.brief or {}).get("positioning")
            else ""
        ),
    )
    out = [LLMMessage(role="system", content=system)]
    for m in turns:
        content = str(m.get("content") or m.get("text") or "").strip()
        out.append(LLMMessage(role=str(m["role"]), content=content[:_MAX_MSG_LEN]))
    return out


async def chat_stream(plan: PromoPlan, messages: list[dict]) -> AsyncIterator[tuple[str, object]]:
    """流式研讨:("token", 增量) 若干次,("done", {"reply"}) 收尾。"""
    chat_messages = build_chat_messages(plan, messages)
    adapter = get_adapter_for(Task.PROMO_CHAT)
    full = ""
    async for delta in adapter.stream(chat_messages):
        if not delta:
            continue
        full += delta
        yield "token", delta
    reply = full.strip()
    if not reply:
        raise PromoChatError("模型没有回应,请重试")
    yield "done", {"reply": reply}
