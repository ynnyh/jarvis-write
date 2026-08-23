# app/engines/promo/common.py
# -*- coding: utf-8 -*-
"""宣传片引擎公共件:角度目录、行序列化。

裁剪/整数收敛等纯函数复用漫剧侧的(common.py 里本就是通用件);
画风方向目录直接复用 DRAMA_DIRECTIONS(宣传片默认 live:空镜为主无恐怖谷)。
"""
from __future__ import annotations

from app.db.models import PromoPlan, PromoShot
from app.engines.drama.common import (
    DRAMA_DIRECTIONS,
    clip,  # noqa: F401 — 引擎各模块经此处转引
    coerce_int,  # noqa: F401
    direction_directive,
    direction_label,
)

# 宣传片切入角度目录:key 白名单 + 给提示词的方向描述
PROMO_ANGLES: list[dict] = [
    {"key": "tour", "label": "旅游风光", "directive": "地标打卡与自然风光,明信片式美感,适合大气开场与收束"},
    {"key": "food", "label": "美食烟火", "directive": "从市井小吃到头汤蒸汽,以食见城,烟火气与人物劳作特写"},
    {"key": "culture", "label": "人文历史", "directive": "古迹、非遗、手艺人,时间感与匠心,节奏可舒缓"},
    {"key": "night", "label": "夜经济夜游", "directive": "夜市、灯光、livehouse 与深夜食肆,霓虹与人气"},
    {"key": "tech", "label": "科技产业", "directive": "园区、实验室、生产线与现代天际线,冷色与速度感"},
    {"key": "nature", "label": "自然生态", "directive": "山水、湿地、四季物候,航拍与长焦,呼吸感"},
    {"key": "guochao", "label": "国潮非遗", "directive": "传统元素的当代表达:汉服、国风音乐、老字号新玩法"},
    {"key": "festival", "label": "节庆事件", "directive": "庙会、灯会、马拉松等事件的高密度高情绪瞬间"},
]
_ANGLE_MAP = {a["key"]: a for a in PROMO_ANGLES}
VALID_ANGLES = tuple(_ANGLE_MAP)


def angle_labels(keys: list) -> str:
    """角度 keys → 「美食烟火+人文历史」式串(未知 key 原样)。"""
    return "+".join(
        _ANGLE_MAP.get(str(k), {}).get("label", str(k)) for k in (keys or []) if str(k).strip()
    ) or "(未定,AI 与客户研讨中)"


def angles_block(keys: list) -> str:
    """角度 keys → 提示词块(标签 + 方向描述)。"""
    items = [k for k in (keys or []) if str(k) in _ANGLE_MAP]
    if not items:
        return "(角度未定——这正是研讨要解决的第一件事)"
    return "\n".join(
        f"- {_ANGLE_MAP[k]['label']}:{_ANGLE_MAP[k]['directive']}" for k in items
    )


def direction_block(direction: str) -> str:
    return f"{direction_label(direction)}——{direction_directive(direction)}"


STATUS_CN = {
    "draft": "企划中",
    "briefed": "简报已定",
    "scripted": "解说词已定",
    "storyboarded": "分镜已定",
    "ready": "提示词就绪",
}


def plan_dict(plan: PromoPlan) -> dict:
    return {
        "id": plan.id,
        "subject": plan.subject,
        "title": plan.title,
        "angles": plan.angles or [],
        "duration_s": plan.duration_s,
        "direction": plan.direction or "live",
        "direction_label": direction_label(plan.direction or "live"),
        "style_name": plan.style_name,
        "style_cn": plan.style_cn,
        "style_en": plan.style_en,
        "negative": plan.negative,
        "landmarks": plan.landmarks or [],
        "material_notes": plan.material_notes,
        "chat_log": plan.chat_log or [],
        "brief": plan.brief or {},
        "brief_locked": plan.brief_locked,
        "script": plan.script or {},
        "pack": plan.pack or {},
        "chunks": plan.chunks or {},
        "status": plan.status,
    }


def shot_dict(shot: PromoShot) -> dict:
    return {
        "id": shot.id,
        "promo_id": shot.promo_id,
        "seq": shot.seq,
        "scene_name": shot.scene_name,
        "characters": shot.characters or [],
        "action_desc": shot.action_desc,
        "shot_type": shot.shot_type,
        "camera": shot.camera,
        "dialogue": shot.dialogue,
        "duration_s": shot.duration_s,
        "prompt_cn": shot.prompt_cn,
        "prompt_en": shot.prompt_en,
        "negative": shot.negative,
    }
