# app/engines/media/anchors.py
# -*- coding: utf-8 -*-
"""画风锚/负面词兜底单点:LLM 漏了锚段就确定性地前置补上。

为什么要单点化:同一段五行代码在 `drama/prompt_render`、`drama/trailer`、
`promo/prompt_render`、`promo/chunks`、`clips/batch` 各抄了一份,
其中英文锚的分隔符还抄歪了——四份用中文句号(`ink-wash。blade light`),
只有 `clips/batch` 用英文逗号。图生视频的英文提示词里塞中文句号是脏数据,
统一按语言选分隔符:中文 `。`,英文 `, `。

「兜底」而不是「强制前置」:LLM 已经把锚段写进提示词时不重复注入
(重复的风格描述会让模型加权过头,画面越跑越偏)。
"""
from __future__ import annotations

STYLE_PREFIX_CN = "【画风锚】"


def ensure_anchor(prompt: str, anchor: str, prefix: str = "", sep: str = "。") -> str:
    """锚段兜底:`prompt` 里没含 `anchor` 时,以 `prefix+anchor+sep` 前置拼接。

    锚段为空、或已经出现在提示词里,都原样返回。
    """
    if not anchor or anchor in prompt:
        return prompt
    return f"{prefix}{anchor}{sep}{prompt}"


def ensure_style_anchors(
    prompt_cn: str, prompt_en: str, style_cn: str, style_en: str
) -> tuple[str, str]:
    """中英双轨画风锚兜底(全站唯一口径)。

    中文:`【画风锚】<风格>。<提示词>`;英文:`<style>, <prompt>`。
    """
    return (
        ensure_anchor(prompt_cn, style_cn or "", STYLE_PREFIX_CN, "。"),
        ensure_anchor(prompt_en, style_en or "", "", ", "),
    )


def merge_negative(negative: str, base: str) -> str:
    """负面词合并:风格卡的通用负面词并进这一格的负面词,已包含则不重复。"""
    base = (base or "").strip()
    negative = (negative or "").strip()
    if not base or base in negative:
        return negative
    return f"{base},{negative}" if negative else base
