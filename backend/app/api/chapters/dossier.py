# app/api/chapters/dossier.py
# -*- coding: utf-8 -*-
"""本章作战图(docs/19 M1):写前一屏看全本章的纲与账。

**纯确定性投影,零抽取**——聚合的全是既有数据:大纲(含梗兑现拍)、核心梗卡、
场景卡、出场人物(大纲人物 × 圣经实体按名/别名匹配)及其关系边(按章时序过滤)、
伏笔账(本章埋/收/强化 + 逾期未收)、上一章章末契约的未回收钩子(承上)与本章
戏核(启下)。没有的数据如实缺省(梗未建/人物未入圣经/无场景切分),前端降级
展示——不填 0 冒充"没问题"。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import (
    Chapter,
    ChapterState,
    Entity,
    Foreshadowing,
    Outline,
    Premise,
    Relationship,
    Scene,
    User,
)
from app.db.session import get_db

router = APIRouter()


class OutlineBrief(BaseModel):
    chapter_number: int
    title: str
    chapter_role: str
    chapter_purpose: str
    suspense_level: str
    emotional_tone: str
    foreshadowing: str
    summary: str
    scene_anchor: str
    premise_beat: str
    characters_involved: list[str] = []
    beats: list[str] = []


class PremiseBrief(BaseModel):
    high_concept: str
    payoff: str
    beats: list[str] = []
    boundaries: list[str] = []
    hook_plan: dict = {}
    source: str = "ai"


class SceneBrief(BaseModel):
    seq: int
    title: str
    location: str
    emotion_target: str
    tension_level: int
    status: str


class RelationBrief(BaseModel):
    from_name: str
    to_name: str
    relation: str


class CharacterBrief(BaseModel):
    name: str
    entity_id: int | None = None
    matched: bool = False
    relations: list[RelationBrief] = []


class ForeShadowItem(BaseModel):
    id: int
    description: str
    status: str
    expected_payoff_chapter: int | None = None


class ForeShadowAccount(BaseModel):
    planted: list[ForeShadowItem] = []
    paid_off: list[ForeShadowItem] = []
    reinforced: list[ForeShadowItem] = []
    overdue: list[ForeShadowItem] = []


class DossierOut(BaseModel):
    chapter_number: int
    outline: OutlineBrief
    premise: PremiseBrief | None = None
    scenes: list[SceneBrief] = []
    characters: list[CharacterBrief] = []
    foreshadows: ForeShadowAccount
    # 承上:上一章章末契约的未回收钩子(缺上一章/契约未提取 → 空串)
    prev_threads: list[str] = []
    prev_location: str = ""


def _entity_aliases(entity: Entity) -> list[str]:
    try:
        return [str(a) for a in (entity.aliases or []) if str(a).strip()]
    except Exception:  # noqa: BLE001 — 脏别名不能挡档案
        return []


def _load_contract(db: Session, project_id: int, chapter_number: int) -> dict:
    row = (
        db.query(ChapterState)
        .join(Chapter, ChapterState.chapter_id == Chapter.id)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if row is None or not row.contract:
        return {}
    try:
        data = json.loads(row.contract)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 脏契约当没有
        return {}


@router.get("/{chapter_number}/dossier", response_model=DossierOut)
def chapter_dossier(
    chapter_number: int,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    if outline is None:
        return DossierOut(
            chapter_number=chapter_number,
            outline=OutlineBrief(
                chapter_number=chapter_number, title="", chapter_role="",
                chapter_purpose="", suspense_level="", emotional_tone="",
                foreshadowing="", summary="", scene_anchor="", premise_beat="",
            ),
            foreshadows=ForeShadowAccount(),
        )

    # ---- 梗卡 ----
    premise_row = (
        db.query(Premise)
        .filter(Premise.project_id == project_id, Premise.kind == "main")
        .first()
    )
    premise = None
    if premise_row is not None:
        premise = PremiseBrief(
            high_concept=premise_row.high_concept,
            payoff=premise_row.payoff,
            beats=premise_row.beats or [],
            boundaries=premise_row.boundaries or [],
            hook_plan=premise_row.hook_plan or {},
            source=premise_row.source,
        )

    # ---- 场景卡(做过场景切分才有;没切分前端回落到 beats)----
    scenes = (
        db.query(Scene)
        .filter(Scene.outline_id == outline.id)
        .order_by(Scene.seq)
        .all()
    )

    # ---- 出场人物 × 圣经实体(按名/别名),及本章有效的两两关系 ----
    involved = [str(n).strip() for n in (outline.characters_involved or []) if str(n).strip()]
    entities = db.query(Entity).filter(Entity.project_id == project_id).all()
    by_name: dict[str, Entity] = {}
    for e in entities:
        for key in [e.name, *_entity_aliases(e)]:
            if key:
                by_name.setdefault(key, e)
    matched: dict[int, CharacterBrief] = {}
    characters: list[CharacterBrief] = []
    for name in involved:
        ent = by_name.get(name)
        brief = CharacterBrief(name=name, entity_id=ent.id if ent else None, matched=ent is not None)
        characters.append(brief)
        if ent is not None:
            matched[ent.id] = brief

    def _valid_at(rel: Relationship) -> bool:
        # 关系边带时序:本章必须落在生效区间内(端点闭区间)
        if rel.valid_from > chapter_number:
            return False
        return rel.valid_until is None or rel.valid_until >= chapter_number

    relations = (
        db.query(Relationship)
        .filter(Relationship.project_id == project_id)
        .all()
    )
    name_of = {e.id: e.name for e in entities}
    for rel in relations:
        if rel.from_entity_id not in matched or rel.to_entity_id not in matched:
            continue  # 作战图只画本章出场人物之间的关系
        if not _valid_at(rel):
            continue
        matched[rel.from_entity_id].relations.append(
            RelationBrief(
                from_name=name_of.get(rel.from_entity_id, "?"),
                to_name=name_of.get(rel.to_entity_id, "?"),
                relation=rel.relation,
            )
        )

    # ---- 伏笔账 ----
    fors = db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id).all()
    account = ForeShadowAccount()

    def item(f: Foreshadowing) -> ForeShadowItem:
        return ForeShadowItem(
            id=f.id, description=f.description, status=f.status,
            expected_payoff_chapter=f.expected_payoff_chapter,
        )

    for f in fors:
        if f.chapter_planted == chapter_number:
            account.planted.append(item(f))
        if f.payoff_chapter == chapter_number:
            account.paid_off.append(item(f))
        if chapter_number in (f.reinforcement_chapters or []):
            account.reinforced.append(item(f))
        if (
            f.expected_payoff_chapter is not None
            and f.expected_payoff_chapter < chapter_number
            and f.status in ("planted", "reinforced")
        ):
            account.overdue.append(item(f))

    # ---- 承上:上一章章末契约的未回收钩子 ----
    prev = _load_contract(db, project_id, chapter_number - 1)
    threads = prev.get("open_threads") or []
    if isinstance(threads, list):
        prev_threads = [str(t) for t in threads if str(t).strip()]
    else:
        prev_threads = []

    return DossierOut(
        chapter_number=chapter_number,
        outline=OutlineBrief(
            chapter_number=outline.chapter_number,
            title=outline.title,
            chapter_role=outline.chapter_role,
            chapter_purpose=outline.chapter_purpose,
            suspense_level=outline.suspense_level,
            emotional_tone=outline.emotional_tone,
            foreshadowing=outline.foreshadowing,
            summary=outline.summary,
            scene_anchor=outline.scene_anchor,
            premise_beat=outline.premise_beat or "",
            characters_involved=involved,
            beats=[str(b) for b in (outline.beats or [])],
        ),
        premise=premise,
        scenes=[
            SceneBrief(
                seq=s.seq, title=s.title, location=s.location,
                emotion_target=s.emotion_target, tension_level=s.tension_level,
                status=s.status,
            )
            for s in scenes
        ],
        characters=characters,
        foreshadows=account,
        prev_threads=prev_threads,
        prev_location=str(prev.get("location") or ""),
    )
