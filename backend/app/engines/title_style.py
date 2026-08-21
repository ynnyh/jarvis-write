# app/engines/title_style.py
# -*- coding: utf-8 -*-
"""章节标题风格预设:全站单一来源。

- 硬约束(不标题党 / 4-12 字 / 像出版目录 / 不用感叹号)常驻在各 prompt 里,
  这里只管"腔调/风味"这一层(flavor directive)。
- 蓝图批量生成(Pillar 2)、批量重出标题(Pillar 3)、单章改名(outline_retitle)
  三处共用这份预设,`plain` 即三处的默认导向,避免同一句文案在多处漂移。
- 前端只需按 key 渲染 chip 文案并回传 key;directive 正文一律在后端解析,
  保证措辞唯一权威(见 resolve_title_directive)。
"""
from __future__ import annotations

# key -> 风格导向(flavor)。key 与前端 TitleStyleControl 的 chip 一一对应。
TITLE_STYLE_PRESETS: dict[str, str] = {
    "plain": "朴素、准确、不夸张,别用浮夸的大词和感叹句,像正经出版小说的目录",
    "hook": "带钩子、有悬念和记忆点,让人想点开往下看,但不剧透关键反转、不喊口号、不堆大词",
    "suspense": "冷峻、克制,用具体的意象或物象营造悬疑与不安,不直白点题、不煽情",
    "poetic": "含蓄、有意境、善用留白,用具象的意象而非抽象大词,不点题、不抒情煽情",
}

# 默认风格:未选任何预设时的兜底,也是单章改名的默认导向
DEFAULT_TITLE_STYLE = "plain"
DEFAULT_TITLE_DIRECTIVE = TITLE_STYLE_PRESETS[DEFAULT_TITLE_STYLE]


def resolve_title_directive(preset: str = "", extra: str = "") -> str:
    """把「预设 key + 可选自由文本」解析成一句最终的标题风格导向。

    - preset 命中预设 → 用该预设文案;未命中 / 空 → 回落默认(plain)。
    - extra 为用户自由文本,非空时追加在预设之后(细化,而非覆盖)——
      对应前端「预设档 + 可选自由文本」:chip 定基调,自由文本补要求。
    """
    base = TITLE_STYLE_PRESETS.get((preset or "").strip(), DEFAULT_TITLE_DIRECTIVE)
    extra = (extra or "").strip()
    return f"{base};另外还要:{extra}" if extra else base
