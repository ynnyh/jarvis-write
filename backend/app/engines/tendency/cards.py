# app/engines/tendency/cards.py
# -*- coding: utf-8 -*-
"""写作手法卡渲染:把作者为本书启用的手法卡拼成可注入 Prompt 的文本块。

与「创作偏好档案」(assembler.py 的 _profile)的分工:
- 档案:结构化的整书主张(文风/禁忌/读者定位),最高优先级约束;
- 手法卡:作者可勾选、可排序的具体写法清单,是"怎么写"的技巧库。

注入方式沿用 style_memo 的既有套路(见 pipeline/chapter.py 的注释):把本块
append 到 render_style_block 的产物上,不新增模板占位符 —— draft/finalize/
polish 都吃 {style_directives},一处追加多处生效,避免模板与 format 两处
只改一处导致 KeyError。

软约束定位:手法卡管"文笔怎么写",不得凌驾于情节事实与润色铁律之上,
因此块内文案显式声明"不得改变情节事实"。
"""
from __future__ import annotations

from typing import Iterable, Protocol


class _CardLike(Protocol):
    """结构化鸭子类型:ORM 的 WritingCard 与测试里的假卡都适用。"""

    title: str
    body: str
    enabled: bool
    sort: int


MAX_CARDS = 20  # 单书注入上限,防 token 膨胀(前端也应限制启用数)
MAX_BODY_CHARS = 600  # 单卡正文截断,防单张卡写成长篇挤爆上下文(API 校验共用此上限)

_HEADER = (
    "\n【写作手法卡(作者为本书启用的写法技巧,尽力遵循;"
    "但不得因此改变情节事实,与情节/铁律冲突时以后者为准)】\n"
)


def render_cards_block(cards: Iterable[_CardLike] | None) -> str:
    """把启用的手法卡渲染成注入块;无启用卡时返回空串(该块整体省略)。

    只取 enabled 且 body 非空的卡,按 sort 升序(同 sort 保持入参顺序,
    Python sorted 稳定排序),最多 MAX_CARDS 张,单卡正文截断到
    MAX_BODY_CHARS 字。
    """
    usable = [
        c
        for c in (cards or [])
        if getattr(c, "enabled", False) and str(getattr(c, "body", "") or "").strip()
    ]
    if not usable:
        return ""

    usable = sorted(usable, key=lambda c: getattr(c, "sort", 0) or 0)[:MAX_CARDS]

    lines: list[str] = []
    for i, card in enumerate(usable, start=1):
        title = str(card.title or "").strip() or f"手法 {i}"
        body = str(card.body or "").strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "……"
        lines.append(f"{i}. {title}:{body}")

    return _HEADER + "\n".join(lines) + "\n"
