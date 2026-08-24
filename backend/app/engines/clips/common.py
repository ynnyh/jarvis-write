# app/engines/clips/common.py
# -*- coding: utf-8 -*-
"""情绪短片引擎公共件:主题目录、序列化、字典版切段(复用漫剧 SRT 内核的时间轴口径)。"""
from __future__ import annotations

from app.db.models import MoodClip
from app.engines.drama.common import coerce_int, direction_directive, direction_label
from app.engines.media.segments import plan_chunks

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


def theme_label(clip: MoodClip) -> str:
    """主题显示:目录 key → 标签+导演提示;自定义 → 原文。"""
    if clip.theme in _THEME_MAP:
        t = _THEME_MAP[clip.theme]
        return f"{t['label']}({t['directive']})"
    return clip.custom_theme.strip() or "(未定)"


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
