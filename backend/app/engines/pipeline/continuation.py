# app/engines/pipeline/continuation.py
# -*- coding: utf-8 -*-
"""章尾续写(ghost text):顺着已写正文续写一个自然段。

轻量单次调用(Task.DRAFT,发散档),不做事实抽取/校验,不落库。
前端在正文末尾以灰字提示,作者按 Tab 接受后作为新段落追加(写回走既有 content 链路)。
上下文=本章蓝图摘要 + 前情摘要 + 已写正文尾部;只推进当下这一小步,不铺展、不收束全章。
"""
from __future__ import annotations

from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import CONTINUE_TAIL_PROMPT

_MAX_TAIL_CHARS = 800   # 注入 prompt 的已写正文尾部上限(够衔接即可,控 token 与延迟)
_MAX_CONT_CHARS = 500   # 续写结果上限:ghost 只提示一小段,超长按段落边界截断


def _clean_continuation(raw: str) -> str:
    """清洗续写结果:去代码围栏/首尾空白,只取首个自然段(遇空行截断),再限长。

    模型偶尔会一口气写好几段或加解释,ghost 只需紧接的一小段,故按首个空行切断;
    若整体仍超长(未分段的长段),按 _MAX_CONT_CHARS 硬截并回退到最后一个句末标点。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        # 去掉 ```lang 开头行与结尾 ``` 围栏
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 只取首个自然段(第一处空行之前)
    para = text.split("\n\n", 1)[0].strip()
    # 段内若含单换行(模型分了行),压成一段连续正文
    para = " ".join(seg.strip() for seg in para.splitlines() if seg.strip())
    if len(para) > _MAX_CONT_CHARS:
        cut = para[:_MAX_CONT_CHARS]
        # 回退到最后一个句末标点,避免截在半句
        for punct in ("。", "!", "?", "”", "…"):
            idx = cut.rfind(punct)
            if idx >= _MAX_CONT_CHARS // 2:
                cut = cut[: idx + 1]
                break
        para = cut
    return para


async def continue_tail(
    chapter_summary: str,
    rolling_summary: str,
    tail: str,
    note: str = "",
    voice_block: str = "",
) -> str:
    """续写正文结尾的一个自然段(ghost text)。

    - chapter_summary:本章蓝图摘要(方向锚点);rolling_summary:前情摘要(连贯用)。
    - tail:已写正文的结尾片段(调用方已按需截断);note:作者额外要求(可空)。
    - voice_block:文风范本(去 AI 味正向锚,API 层从创作偏好档案取;引擎侧默认空)。
    返回续写的一段正文(纯文本)。tail 为空 → ValueError(无处可续)。
    """
    tail = (tail or "").strip()
    if not tail:
        raise ValueError("本章还没有正文,先写或生成一段再续写")

    raw = await get_adapter_for(Task.DRAFT).ask(
        CONTINUE_TAIL_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            rolling_summary=rolling_summary.strip() or "(无)",
            note=note.strip() or "(无)",
            tail=tail[-_MAX_TAIL_CHARS:],
            voice_block=voice_block,
        )
    )
    continuation = _clean_continuation(raw)
    if not continuation:
        raise ValueError("模型没有续出内容,请重试")
    return continuation
