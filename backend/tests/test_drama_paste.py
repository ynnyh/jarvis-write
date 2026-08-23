# tests/test_drama_paste.py
# -*- coding: utf-8 -*-
"""按平台拼的粘贴版提示词(paste.py):负面词不能在复制时丢失。

真实场景:用户的生图站(GPT-image / 豆包一类)只有一个描述框,没有负面框。
以前只能分轨复制,负面词粘不进去 = 白给。这里钉住三种站的粘贴版口径。
"""
from __future__ import annotations

from app.engines.drama.paste import (
    DEFAULT_PLATFORM,
    PLATFORMS,
    paste_variants,
    ref_sheet_paste,
    shot_paste,
)


class _Shot:
    def __init__(self, cn="正文中文", en="english prompt", neg="文字水印、多余手指"):
        self.prompt_cn, self.prompt_en, self.negative = cn, en, neg


class _Style:
    def __init__(self, ratio="9:16", negative="文字水印、五官错位"):
        self.ratio, self.negative = ratio, negative


def _v(**kw):
    base = dict(
        prompt_cn="少年立在雪中",
        prompt_en="a boy standing in snow",
        negative="文字水印、多余手指",
    )
    base.update(kw)
    return paste_variants(**base)


# ---------- 单框站:负面词必须以否定句进正文 ----------

def test_oneframe_folds_negative_into_main():
    v = _v()["oneframe"]
    assert "少年立在雪中" in v["main"]
    assert "【不要出现】文字水印、多余手指" in v["main"]
    assert v["negative"] == ""          # 单框站没有负面框,不能把词留在这
    assert "9:16" in v["main"]          # 比例也得写进正文(站点没有比例选项时靠它)


def test_oneframe_without_negative_has_no_empty_section():
    v = _v(negative="")["oneframe"]
    assert "【不要出现】" not in v["main"]
    assert v["main"].startswith("少年立在雪中")


# ---------- 有负面框的站:正反分开 ----------

def test_dualbox_keeps_negative_separate():
    v = _v()["dualbox"]
    assert v["negative"] == "文字水印、多余手指"
    assert "【不要出现】" not in v["main"]  # 别重复塞一遍
    assert "9:16" in v["hint"]


# ---------- Midjourney:英文 + 参数 ----------

def test_mj_appends_ar_and_no_params():
    v = _v()["mj"]
    assert v["main"].startswith("a boy standing in snow")
    assert "--ar 9:16" in v["main"]
    assert "--no text, watermark" in v["main"]


def test_mj_does_not_double_ar_when_model_already_gave_it():
    v = _v(prompt_en="a boy in snow --ar 9:16")["mj"]
    assert v["main"].count("--ar") == 1


def test_mj_empty_when_no_english_track():
    assert _v(prompt_en="")["mj"]["main"] == ""


# ---------- 参考图指令:只在该格角色有定妆照时出现 ----------

def test_ref_names_add_upload_instruction():
    v = _v(ref_names=["陈小满", "阿七"])["oneframe"]
    assert "「陈小满」、「阿七」" in v["main"]
    assert "严格照参考图" in v["main"]


def test_no_ref_names_no_instruction():
    v = _v()["oneframe"]
    assert "参考图" not in v["main"]
    assert "参考图" not in v["hint"]


def test_blank_ref_names_are_ignored():
    v = _v(ref_names=["  ", ""])["oneframe"]
    assert "参考图" not in v["main"]


# ---------- 定妆照那一版:构图要求不同(正面半身+纯背景) ----------

def test_ref_sheet_kind_asks_for_clean_portrait():
    v = paste_variants(
        prompt_cn="少年,黑短发,粗布灰袍",
        negative="文字水印",
        kind="ref_sheet",
        ratio="3:4",
    )["oneframe"]
    assert "正面半身" in v["main"] and "纯色干净背景" in v["main"]
    assert "3:4" in v["main"]
    assert "多人同框" in v["main"]  # 定妆照必须单人


# ---------- 便利封装:从卡/格取料 ----------

def test_shot_paste_reads_ratio_from_style():
    v = shot_paste(_Shot(), _Style(ratio="16:9"), ref_names=["陈小满"])
    assert "16:9" in v["oneframe"]["main"]
    assert "--ar 16:9" in v["mj"]["main"]


def test_shot_paste_tolerates_missing_style():
    v = shot_paste(_Shot(), None)
    assert "9:16" in v["oneframe"]["main"]  # 回落默认竖屏


def test_ref_sheet_paste_uses_card_prompt_and_style_negative():
    class _Card:
        ref_prompt_cn = "少年定妆照,黑短发"
        ref_prompt_en = "boy character sheet"

    v = ref_sheet_paste(_Card(), _Style())
    assert "少年定妆照" in v["oneframe"]["main"]
    assert "文字水印、五官错位" in v["oneframe"]["main"]  # 负面走风格卡基座
    assert v["dualbox"]["negative"] == "文字水印、五官错位"


def test_ref_sheet_paste_empty_card_is_harmless():
    class _Blank:
        pass

    v = ref_sheet_paste(_Blank(), None)
    assert v["oneframe"]["main"].startswith("【构图】")  # 只剩构图要求,不炸


# ---------- 平台清单:前端下拉的数据源 ----------

def test_platform_list_matches_variant_keys():
    keys = {k for k, _ in PLATFORMS}
    assert keys == set(_v().keys())
    assert DEFAULT_PLATFORM in keys
    assert PLATFORMS[0][0] == DEFAULT_PLATFORM  # 默认项排第一
