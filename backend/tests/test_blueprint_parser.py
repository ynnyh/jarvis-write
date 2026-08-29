# tests/test_blueprint_parser.py
# -*- coding: utf-8 -*-
"""蓝图解析器的格式容忍度回归。

线上事故(2026-08-29):deepseek 系模型把章节头写成 markdown 一级标题
「# 第1章 - 标题」,解析器只容忍 * 加粗前缀 → 整块 0 章,三次续补全废。
钉住:markdown # 前缀、全角冒号分隔、加粗字段行都要解析得出。
"""
from __future__ import annotations

from app.engines.pipeline.blueprint_parser import (
    count_chapter_heads,
    parse_blueprint,
)

_MD_BLUEPRINT = """\
# 第1章 - 血月之邀

**本章定位:** 常规推进
**本章简述:** 周小满被李耀锁进器材室,濒死时系统激活。

---

### 第2章：角斗食堂

**本章定位:** 关键转折
**本章简述:** 进入角斗食堂副本,被迫与 NPC 搏击。
"""


def test_markdown_headings_and_bold_fields_parse():
    """# / ### 前缀的章节头 + **字段:** 值 的加粗形式,全部解析得出。"""
    chapters = parse_blueprint(_MD_BLUEPRINT)
    assert [c["chapter_number"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "血月之邀"
    assert chapters[1]["title"] == "角斗食堂"  # 全角冒号分隔的章节头
    assert chapters[0]["chapter_role"] == "常规推进"
    assert chapters[0]["chapter_role"] != "**常规推进**"
    assert chapters[1]["chapter_role"] == "关键转折"


def test_count_chapter_heads_tolerates_markdown():
    """流式进度的章节头计数与解析器同一口径:markdown 标题也要数得出来。"""
    assert count_chapter_heads(_MD_BLUEPRINT) == 2
    assert count_chapter_heads("**第12章 - 追猎**") == 1
    assert count_chapter_heads("没有章节头的文本") == 0


def test_plain_head_still_parses():
    """最朴素的形式不许被新容忍度破坏:第1章 - 标题 照常解析。"""
    chapters = parse_blueprint("第1章 - 雨夜\n本章简述:开场。\n")
    assert chapters[0]["chapter_number"] == 1
    assert chapters[0]["title"] == "雨夜"
