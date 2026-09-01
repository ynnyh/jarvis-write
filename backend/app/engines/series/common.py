# app/engines/series/common.py
# -*- coding: utf-8 -*-
"""角色系列短片共用口径:字段上限、状态目录、行 → dict 序列化。

时长与字数是这条线的产品决策(用户明确要求):
- 时长自由输入 5-15 秒(不走三线的 15/30 固定档);
- 提示词**篇幅自由、不设下限**:短到一两百字、长到上千字都收,
  细节密度按剧情需要(用户原话:允许超过五百或一千,但没必要强制)。
  引擎只挡空壳输出(空串=没写成,重试),不卡字数。
"""
from __future__ import annotations

from app.engines.media.directions import direction_label
from app.engines.media.text import clip

MIN_DURATION_S = 5
MAX_DURATION_S = 15

NAME_MAX = 60        # 角色名
BRIEF_MAX = 500      # 建角色的一句话概念
LOOK_MAX = 2000      # 定妆描述(长短不拘,只挡空;上限 2000 防跑飞)
PLOT_MAX = 1000      # 单集剧情
TITLE_MAX = 24       # 集标题(存 12 字上一点,宽进严出)
PROMPT_MAX = 2000    # 提示词字数上限(允许超一千,只防极端跑飞;超了截断存档)
NEGATIVE_MAX = 200   # 负面词上限
HINTS_MAX = 80       # 氛围关键词

STATUS_CN = {"draft": "待生成", "generating": "生成中", "done": "已出词"}

VALID_DIRECTIONS_EXCLUDE = ("auto",)  # 系列角色不走「AI 按书定」——那是书的语境


def character_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "look": row.look,
        "direction": row.direction,
        "direction_label": direction_label(row.direction),
        "default_duration_s": row.default_duration_s,
        "style_hints": row.style_hints,
        "ref_images": list(row.ref_images or []),
    }


def episode_dict(row) -> dict:
    return {
        "id": row.id,
        "character_id": row.character_id,
        "plot": row.plot,
        "duration_s": row.duration_s,
        "status": row.status,
        "status_cn": STATUS_CN.get(row.status, row.status),
        "output": dict(row.output or {}),
    }


def norm_output(raw: dict | None, fallback_title: str = "") -> dict:
    """归一化单集输出(生成与手改同口径):title/prompt_cn/negative 三件套。"""
    raw = raw if isinstance(raw, dict) else {}
    prompt_cn = str(raw.get("prompt_cn") or "").strip()[:PROMPT_MAX]
    negative = str(raw.get("negative") or "").strip()[:NEGATIVE_MAX]
    title = str(raw.get("title") or "").strip()[:TITLE_MAX]
    return {
        "title": title or clip(fallback_title, 12),
        "prompt_cn": prompt_cn,
        "negative": negative,
    }
