# tests/test_title_style.py
# -*- coding: utf-8 -*-
"""章节标题风格预设 + resolve_title_directive:Pillar 2/3 与单章改名共用的单一来源。"""
from app.engines.title_style import (
    DEFAULT_TITLE_DIRECTIVE,
    DEFAULT_TITLE_STYLE,
    TITLE_STYLE_PRESETS,
    resolve_title_directive,
)


def test_presets_cover_four_styles_and_default():
    # 四个前端 chip 的 key 必须都在
    assert set(TITLE_STYLE_PRESETS) == {"plain", "hook", "suspense", "poetic"}
    assert DEFAULT_TITLE_STYLE == "plain"
    assert DEFAULT_TITLE_DIRECTIVE == TITLE_STYLE_PRESETS["plain"]
    assert all(v.strip() for v in TITLE_STYLE_PRESETS.values())


def test_resolve_picks_preset():
    assert resolve_title_directive("hook") == TITLE_STYLE_PRESETS["hook"]
    assert resolve_title_directive("suspense") == TITLE_STYLE_PRESETS["suspense"]


def test_resolve_falls_back_to_default_on_empty_or_unknown():
    assert resolve_title_directive("") == DEFAULT_TITLE_DIRECTIVE
    assert resolve_title_directive("不存在的档") == DEFAULT_TITLE_DIRECTIVE
    assert resolve_title_directive() == DEFAULT_TITLE_DIRECTIVE


def test_resolve_appends_free_text_after_preset():
    out = resolve_title_directive("plain", "偏古典、带地名")
    assert out.startswith(TITLE_STYLE_PRESETS["plain"])
    assert "偏古典、带地名" in out
    # 自由文本是追加细化,不是覆盖预设
    assert TITLE_STYLE_PRESETS["plain"] in out


def test_resolve_free_text_only_uses_default_base():
    out = resolve_title_directive("", "全部用四字短语")
    assert out.startswith(DEFAULT_TITLE_DIRECTIVE)
    assert "全部用四字短语" in out


def test_resolve_trims_whitespace_free_text_to_pure_preset():
    # 只有空白的自由文本视作没填,回落纯预设(不留下空的"另外还要:")
    assert resolve_title_directive("hook", "   ") == TITLE_STYLE_PRESETS["hook"]
    assert "另外还要" not in resolve_title_directive("hook", "  ")
