# app/engines/drama/common.py
# -*- coding: utf-8 -*-
"""漫剧引擎公共件:上下文拼装、LLM 输出裁剪、行序列化。

只依赖 db 模型与 LLM 适配器,不碰 HTTP 概念(照 polish 引擎的分层惯例)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    Chapter,
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    Entity,
    Outline,
    Project,
)

# 改编模式 → 提示词里的说明文案
MODE_DESC = {
    "dialogue": "对白演绎(角色台词驱动画面,主流漫剧形态)",
    "narration": "口播解说(旁白讲故事,画面配图,解说漫形态)",
}
VALID_MODES = ("dialogue", "narration")

# 景别/运镜白名单(normalize 用,白名单外的值截短保留——LLM 偶尔写"大特写"也放行)
SHOT_TYPES = ("远景", "全景", "中景", "近景", "特写")
CAMERAS = ("固定", "推", "拉", "摇", "跟随", "环绕")


def clip(s: object, width: int) -> str:
    """LLM 字段裁剪:转字符串、去首尾空白、限长。"""
    return str(s or "").strip()[:width]


def coerce_int(raw: object, default: int, lo: int = 0, hi: int = 10**6) -> int:
    """把 LLM 的 "4"/4.0/脏值收敛成 [lo, hi] 内的 int。"""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def concept_block(project: Project) -> str:
    """结构化概念六字段渲染成提示词块(与 api/submission 同格式,引擎层自持一份)。"""
    c = project.concept or {}
    if not isinstance(c, dict):
        return ""
    labels = {
        "logline": "一句话故事", "hook": "钩子", "twist": "反转",
        "protagonist": "主角", "conflict": "核心冲突", "setting": "世界观/设定",
    }
    lines = [f"  {labels[k]}:{c[k]}" for k in labels if c.get(k)]
    return "【故事概念】\n" + "\n".join(lines) + "\n" if lines else ""


def style_memo_block(project: Project) -> str:
    if project.style_memo and project.style_memo.strip():
        return f"【文风备忘(小说侧,供气质参考)】{project.style_memo.strip()[:300]}\n"
    return ""


def approved_chapter_numbers(db: Session, project_id: int) -> list[int]:
    """已定稿(approved)的章号列表——漫剧工坊的准入门槛与章节范围数据源。"""
    rows = (
        db.query(Chapter.chapter_number)
        .filter(Chapter.project_id == project_id, Chapter.status == "approved")
        .order_by(Chapter.chapter_number)
        .all()
    )
    return [n for (n,) in rows]


def chapter_final_text(db: Session, project_id: int, chapter_number: int) -> str:
    """取某章定稿正文(approved 后正文在 final_content;兜底 draft_content)。"""
    row = (
        db.query(Chapter.final_content, Chapter.draft_content)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .order_by(Chapter.chapter_number)
        .first()
    )
    if not row:
        return ""
    return (row.final_content or row.draft_content or "").strip()


# =============== 行 → dict 序列化(API 响应/导出共用) ===============

def style_card_dict(card: DramaStyleCard | None) -> dict | None:
    if card is None:
        return None
    return {
        "id": card.id,
        "style_name": card.style_name,
        "style_cn": card.style_cn,
        "style_en": card.style_en,
        "negative": card.negative,
        "ratio": card.ratio,
    }


def character_card_dict(card: DramaCharacterCard) -> dict:
    return {
        "id": card.id,
        "entity_id": card.entity_id,
        "name": card.name,
        "appearance_cn": card.appearance_cn,
        "appearance_en": card.appearance_en,
        "outfit_cn": card.outfit_cn,
        "voice_desc": card.voice_desc,
        "locked": card.locked,
    }


def scene_card_dict(card: DramaSceneCard) -> dict:
    return {
        "id": card.id,
        "name": card.name,
        "appearance_cn": card.appearance_cn,
        "appearance_en": card.appearance_en,
    }


def episode_dict(ep: DramaEpisode) -> dict:
    return {
        "id": ep.id,
        "ep_index": ep.ep_index,
        "title": ep.title,
        "source_chapter": ep.source_chapter,
        "hook": ep.hook,
        "recap": ep.recap,
        "cliffhanger": ep.cliffhanger,
        "mode": ep.mode,
        "duration_target_s": ep.duration_target_s,
        "script": ep.script or {},
        "status": ep.status,
    }


def shot_dict(shot: DramaShot) -> dict:
    return {
        "id": shot.id,
        "episode_id": shot.episode_id,
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


# =============== 资产索引(prompt_render 按名匹配用) ===============

def character_anchor_maps(
    db: Session, project_id: int
) -> tuple[dict[str, DramaCharacterCard], dict[str, DramaCharacterCard]]:
    """返回 (名字→卡, 别名→卡) 两个映射。别名来自故事圣经 Entity.aliases,
    让 LLM 在分镜里用旧称呼/小名也能命中同一张角色卡。"""
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .all()
    )
    by_name = {c.name: c for c in cards}
    by_alias: dict[str, DramaCharacterCard] = {}
    entity_ids = [c.entity_id for c in cards if c.entity_id]
    if entity_ids:
        entities = {
            e.id: e
            for e in db.query(Entity).filter(Entity.id.in_(entity_ids)).all()
        }
        for c in cards:
            e = entities.get(c.entity_id) if c.entity_id else None
            for alias in ((e.aliases if e else None) or []):
                a = str(alias).strip()
                if a:
                    by_alias.setdefault(a, c)
    return by_name, by_alias


def match_character(
    name: str,
    by_name: dict[str, DramaCharacterCard],
    by_alias: dict[str, DramaCharacterCard],
) -> DramaCharacterCard | None:
    n = name.strip()
    return by_name.get(n) or by_alias.get(n)


def scene_anchor_map(db: Session, project_id: int) -> dict[str, DramaSceneCard]:
    cards = (
        db.query(DramaSceneCard)
        .filter(DramaSceneCard.project_id == project_id)
        .all()
    )
    return {c.name: c for c in cards}


def outline_rows(db: Session, project_id: int, from_ch: int, to_ch: int) -> list[Outline]:
    return (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number >= from_ch,
            Outline.chapter_number <= to_ch,
        )
        .order_by(Outline.chapter_number)
        .all()
    )
