# app/engines/pipeline/order_block.py
# -*- coding: utf-8 -*-
"""章节订单 → 提示词槽位替换文本(docs/20 订单制 §5.2)。

纪律(§14 影响面 #1):订单**替换**草稿/定稿模板里既有的槽位
(涉及人物/本章简述/本章节拍/伏笔操作),不新增占位符、不追加额外块——
模板与 format 两处只改一处导致 KeyError 的坑不再挖第二遍。

无订单 / payload 为空 / 某单为空:对应槽位维持蓝图行原值,逐槽独立降级。
"""
from __future__ import annotations

from typing import Any


def _s(v: Any) -> str:
    return str(v or "").strip()


def _cast_line(cast: dict) -> str:
    """人物进出单 → 「涉及人物」槽位:必登场/在场/退场三段一句带状态。"""
    parts: list[str] = []
    entering = [
        f"{_s(e.get('name'))}(为何此时入场:{_s(e.get('reason')) or '作者安排'})"
        for e in (cast.get("entering") or [])
        if isinstance(e, dict) and _s(e.get("name"))
    ]
    if entering:
        parts.append("必登场:" + "、".join(entering))
    present = [_s(n) for n in (cast.get("present") or []) if _s(n)]
    if present:
        parts.append("在场:" + "、".join(present))
    exiting = [
        "{}({}{})".format(
            _s(e.get("name")),
            _s(e.get("mode")) or "退场",
            f";退场前须收:{_s(e.get('threads'))}" if _s(e.get("threads")) else "",
        )
        for e in (cast.get("exiting") or [])
        if isinstance(e, dict) and _s(e.get("name"))
    ]
    if exiting:
        parts.append("本章退场:" + "、".join(exiting))
    return ";".join(parts)


def _relations_lines(relations: list) -> list[str]:
    out = []
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        a, b = _s(r.get("from")), _s(r.get("to"))
        if not a or not b:
            continue
        out.append(
            f"{a}—{b}:{_s(r.get('before')) or '现状'} → {_s(r.get('after')) or '变化'}"
            + (f"(触发:{_s(r.get('event'))})" if _s(r.get("event")) else "")
        )
    return out


def _beats_text(beats: list) -> str:
    lines = [_s(b) for b in beats or [] if _s(b)]
    if not lines:
        return ""
    return "\n".join(f"{i}. {b}" for i, b in enumerate(lines, 1))


def _summary_appendix(payload: dict) -> str:
    """订单里「简述槽位装不下」的要点:关系变动/承上必收/章末留钩/作者指令。"""
    lines: list[str] = []
    rels = _relations_lines(payload.get("relations"))
    if rels:
        lines.append("关系变动:" + ";".join(rels))
    hooks = payload.get("hooks") or {}
    must = [
        _s(h.get("text") if isinstance(h, dict) else h)
        for h in (hooks.get("carry_in") or [])
        if isinstance(h, dict) and h.get("must") and _s(h.get("text"))
    ]
    if must:
        lines.append("承上必收(本章必须回收):" + ";".join(must))
    if _s(hooks.get("leave")):
        lines.append("章末留钩:" + _s(hooks.get("leave")))
    if _s(payload.get("free_directive")):
        lines.append("作者指令(务必落实):" + _s(payload.get("free_directive")))
    return "\n".join(lines)


def _fore_text(fore: dict) -> str:
    """伏笔单 → 「伏笔操作」槽位:回收到期项 + 新埋项。"""
    lines: list[str] = []
    for d in (fore.get("payoff") or []):
        if _s(d):
            lines.append("回收:" + _s(d))
    for d in (fore.get("reinforce") or []):
        if _s(d):
            lines.append("强化:" + _s(d))
    for d in (fore.get("plant") or []):
        if _s(d):
            lines.append("新埋:" + _s(d))
    return ";".join(lines)


def order_slots(payload: dict | None) -> dict[str, str] | None:
    """确认订单 → 槽位替换表。四槽各自独立:某单为空就不替换那一槽。

    返回 None = 没有任何槽位需要替换(无订单/空单),调用方零分支走原路径。
    """
    if not isinstance(payload, dict) or not payload:
        return None
    slots: dict[str, str] = {}

    cast_text = _cast_line(payload.get("cast") or {})
    if cast_text:
        slots["characters_involved"] = cast_text

    beats_text = _beats_text(payload.get("beats"))
    if beats_text:
        slots["chapter_beats"] = beats_text

    appendix = _summary_appendix(payload)
    if appendix:
        slots["order_appendix"] = appendix

    fore_text = _fore_text(payload.get("foreshadow") or {})
    if fore_text:
        slots["foreshadowing"] = fore_text

    return slots or None
