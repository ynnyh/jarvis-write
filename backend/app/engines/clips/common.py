# app/engines/clips/common.py
# -*- coding: utf-8 -*-
"""情绪短片引擎公共件:主题/导向维度目录、序列化、字典版切段(时间轴口径同 media.subtitles)。"""
from __future__ import annotations

from app.db.models import MoodClip
from app.engines.media.directions import direction_directive, direction_label
from app.engines.media.segments import plan_chunks
from app.engines.media.text import coerce_int
from app.prompts.clips import (
    dialogue_style_rule,
    intensity_rule,
    pacing_rule,
)

# 情绪主题目录:key 白名单 + 给提示词的「导演提示」(这个情绪怎么拍才戳)
CLIP_THEMES: list[dict] = [
    {"key": "regret", "label": "遗憾", "directive": "错过与未说出口;克制冷藏,最后一格才许破防"},
    {"key": "quarrel", "label": "争吵", "directive": "越吵越近或越吵越远;台词短促有火药味,收在突然的安静"},
    {"key": "love", "label": "爱情", "directive": "具体的爱不是形容词,是动作与细节;避免俗套桥段堆砌"},
    {"key": "childlike", "label": "童趣", "directive": "儿童视角的认真与荒唐;大人世界作背景板,笑点干净"},
    {"key": "missing", "label": "思念", "directive": "不在场的人靠在场之物活着;物件特写是主语"},
    {"key": "lonely", "label": "孤独", "directive": "人群里的孤独比荒野狠;空间大、人小、声音少"},
    {"key": "healing", "label": "治愈", "directive": "小而确定的暖;不煽情,收在释然的一口气"},
    {"key": "hotblood", "label": "热血", "directive": "憋到顶再放;节奏前压后爆,收在起跑/出拳那一帧"},
    {"key": "farewell", "label": "告别", "directive": "告别都不说破;最后一次寻常对话就是告别本身"},
    {"key": "reunion", "label": "重逢", "directive": "千言万语堵在一声称呼上;迟疑的肢体先于语言"},
]
_THEME_MAP = {t["key"]: t for t in CLIP_THEMES}
VALID_THEMES = tuple(_THEME_MAP)

VALID_DURATIONS = (15, 30)

# ---- 导向维度(用户可细化的"方向"):目录 key 白名单 + label(前端展示) ----
# directive 进提示词的部分由 prompts.clips 的 *_rule 函数给(硬约束文案)。
DIALOGUE_STYLES: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "voiceover", "label": "旁白独白"},
    {"key": "dialogue", "label": "对白主导"},
    {"key": "silent", "label": "无台词"},
]
PACINGS: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "hook_first", "label": "爆点前置"},
    {"key": "slow_burn", "label": "层层蓄势"},
    {"key": "twist_end", "label": "结尾反转"},
]
INTENSITIES: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "restrained", "label": "克制留白"},
    {"key": "standard", "label": "标准"},
    {"key": "intense", "label": "浓烈直给"},
]
VALID_DIALOGUE_STYLES = tuple(t["key"] for t in DIALOGUE_STYLES)
VALID_PACINGS = tuple(t["key"] for t in PACINGS)
VALID_INTENSITIES = tuple(t["key"] for t in INTENSITIES)


def theme_label(clip: MoodClip) -> str:
    """主题显示:目录 key → 标签+导演提示;自定义 → 原文 + 兜底导演提示
    (自定义主题没有目录 directive 可靠,不补一句"落地成具体场景"就常拍成抽象意象)。"""
    if clip.theme in _THEME_MAP:
        t = _THEME_MAP[clip.theme]
        return f"{t['label']}({t['directive']})"
    custom = clip.custom_theme.strip()
    if not custom:
        return "(未定)"
    return f"{custom}(自由命题:落地成具体场景与人物关系,别拍成抽象意象)"


def steering_block(clip: MoodClip) -> str:
    """用户导向维度 → 提示词硬约束块(两段共用:auto 的维度不出行,零影响)。"""
    rules = [
        dialogue_style_rule(getattr(clip, "dialogue_style", "") or ""),
        pacing_rule(getattr(clip, "pacing", "") or ""),
        intensity_rule(getattr(clip, "intensity", "") or ""),
    ]
    hints = (getattr(clip, "style_hints", "") or "").strip()
    if hints:
        rules.append(
            f"**氛围关键词(必须自然融入画风卡与各格提示词的氛围)**:{hints}"
        )
    rules = [r for r in rules if r]
    if not rules:
        return ""
    return "【导向(用户指定,硬约束)】" + ";".join(rules) + "\n"


def theme_display(clip: MoodClip) -> str:
    """列表用短标签。"""
    if clip.theme in _THEME_MAP:
        return _THEME_MAP[clip.theme]["label"]
    return clip.custom_theme.strip() or "自定义"


def shot_hint(duration_s: int) -> str:
    """时长 → 建议镜头数(提示词用)。"""
    return "3-5 格,每格 2-6 秒" if duration_s <= 15 else "5-7 格,每格 2-6 秒"


# =============== 切段(复用 media.segments 单点;时间轴与 SRT 同口径) ===============

def group_chunks(shots: list[dict], chunk_s: int) -> list[dict]:
    """把分镜 dict 列表按镜头边界贪心聚段,返回带起止时间码的段列表。"""
    return plan_chunks(shots, chunk_s)


# =============== 序列化 ===============

STATUS_CN = {
    "draft": "待生成",
    "generated": "候选已出",
    "picked": "已选定",
}


def clip_dict(row: MoodClip, with_candidates: bool = True) -> dict:
    d = {
        "id": row.id,
        "source_project_id": row.source_project_id,
        "theme": row.theme,
        "custom_theme": row.custom_theme,
        "theme_display": theme_display(row),
        "duration_s": row.duration_s,
        "direction": row.direction or "live",
        "direction_label": direction_label(row.direction or "live"),
        "inspiration": row.inspiration,
        "dialogue_style": getattr(row, "dialogue_style", "") or "auto",
        "pacing": getattr(row, "pacing", "") or "auto",
        "intensity": getattr(row, "intensity", "") or "auto",
        "style_hints": getattr(row, "style_hints", "") or "",
        "style_name": row.style_name,
        "style_cn": row.style_cn,
        "style_en": row.style_en,
        "negative": row.negative,
        "chosen": row.chosen,
        "clip": row.clip or {},
        "status": row.status,
        "status_cn": STATUS_CN.get(row.status, row.status),
    }
    if with_candidates:
        d["candidates"] = row.candidates or []
    return d
