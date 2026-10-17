# app/engines/intent.py
# -*- coding: utf-8 -*-
"""理解卡:开书确认过的创作意图,全链生成的最高约束(docs/30 / docs/32)。

为什么要有它:传国玉玺案例的病根不是「模型不会写」,而是**没人把作者的世界坐标
写下来**——题材由一次 13 token 的推断静默落库(真实历史 → 架空历史),架构层再
自由发挥,主角从「玉玺」换成了人类太子。理解卡把「世界坐标 / 视角 / 主角」变成
作者确认过的一等事实,下游每一层都拿它当硬约束,而不是靠模型猜。

注入通道:渲染成一块文本,走既有的 style_directives(架构四层)/ style_block
(骨架、蓝图)通道——**模板一字不动**,评测基线指纹不变,差异全在这块文本里
(与 MACRO_PLAN_PROMPT 的 hard_note 同一纪律)。
"""
from __future__ import annotations

from typing import Any

# 卡字段与标签:顺序即渲染顺序(世界坐标在最前,越靠前越是「不许动」的坐标级字段)
INTENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("world", "世界坐标"),
    ("viewpoint", "叙述视角"),
    ("cast", "主角与关键关系"),
    ("flavor", "味道"),
    ("must", "必须保留"),
    ("avoid", "不要出现"),
    ("genre", "题材"),
)

# 坐标级字段:下游任何一层都不得改写/替换(改了就是另一本书)
COORDINATE_FIELDS: tuple[str, ...] = ("world", "viewpoint", "cast", "genre")

_LIST_FIELDS: tuple[str, ...] = ("flavor", "must", "avoid")
_STR_MAX = 600
_LIST_ITEM_MAX = 80
_LIST_CAP = 8
_UNCERTAIN_CAP = 4


def _clean_str(value: Any, cap: int = _STR_MAX) -> str:
    return " ".join(str(value or "").split())[:cap]


def _clean_list(value: Any, cap: int = _LIST_CAP, item_cap: int = _LIST_ITEM_MAX) -> list[str]:
    """列表字段收敛:字符串按顿号/逗号/换行拆,去重、限量、截断。"""
    if isinstance(value, str):
        value = [s for s in value.replace("、", "\n").replace(",", "\n").split("\n")]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = _clean_str(item, item_cap)
        if s and s not in out:
            out.append(s)
    return out[:cap]


def clean_intent_card(raw: Any) -> dict[str, Any]:
    """把模型/前端给的卡收敛成落库形状(白名单键 + 长度上限 + 去重)。

    为什么白名单:卡会被注入每一层 prompt,多余键就是噪声,脏值(None/超长/嵌套
    对象)会让 prompt 变形。收敛在这里一次做干净,下游只读干净数据。
    """
    src = raw if isinstance(raw, dict) else {}
    card: dict[str, Any] = {}
    for key, _label in INTENT_FIELDS:
        if key in _LIST_FIELDS:
            card[key] = _clean_list(src.get(key))
        else:
            card[key] = _clean_str(src.get(key))
    uncertain: list[dict[str, Any]] = []
    known = dict(INTENT_FIELDS)
    for item in src.get("uncertain") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if field not in known:
            continue
        uncertain.append({
            "field": field,
            "reason": _clean_str(item.get("reason"), 150),
            "candidates": _clean_list(item.get("candidates"), 4, 120),
        })
    card["uncertain"] = uncertain[:_UNCERTAIN_CAP]
    return card


def intent_card_empty(card: Any) -> bool:
    """卡是否有实质内容(全空 = 没有这张卡:老项目与未确认项目零影响)。"""
    if not isinstance(card, dict):
        return True
    for key, _label in INTENT_FIELDS:
        value = card.get(key)
        if isinstance(value, list):
            if any(str(x).strip() for x in value):
                return False
        elif str(value or "").strip():
            return False
    return True


def render_intent_block(project: Any) -> str:
    """理解卡 → 注入块;空卡返回空串(prompt 一字不变,行为向后兼容)。

    返回文本以换行开头,便于直接拼在 style_block 之后。
    """
    card = getattr(project, "intent_card", None)
    if intent_card_empty(card):
        return ""
    lines: list[str] = []
    for key, label in INTENT_FIELDS:
        value = card.get(key)
        text = " / ".join(str(x) for x in value) if isinstance(value, list) else str(value or "").strip()
        if text:
            lines.append(f"{label}:{text}")
    if not lines:
        return ""
    coords = "、".join(label for key, label in INTENT_FIELDS if key in COORDINATE_FIELDS)
    return (
        "\n【理解卡:作者已确认的创作意图(最高约束,优先于任何通用写法与惯例)】\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"\n铁律:{coords} 是这本书的坐标级设定,任何一层都不得改写、替换,也不得"
        "「自由发挥」成别的东西;若与其它输入冲突,一律以本卡为准。\n"
    )
