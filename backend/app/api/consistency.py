# app/api/consistency.py
# -*- coding: utf-8 -*-
"""一致性看板接口:故事圣经与伏笔状态的查看,以及人物管理闭环。

GET    /api/projects/{id}/bible?chapter=N        第 N 章时刻的实体状态快照(时序查询)
GET    /api/projects/{id}/foreshadowings         伏笔清单(四态 + 到期提醒)
GET    /api/projects/{id}/characters             人物卡清单(关键事实 + 出场章号)
POST   /api/projects/{id}/characters             新增人物(实体 + 初始事实)
PATCH  /api/projects/{id}/characters/{entity_id} 退场/恢复(退场后生成不再注入)
DELETE /api/projects/{id}/facts/{fact_id}        删除单条事实(修正抽错的)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Entity, Fact, Foreshadowing, Outline
from app.db.session import get_db
from app.engines.consistency import BibleService, ForeshadowScheduler

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["consistency"],
    dependencies=[Depends(get_current_user)],
)


class FactOut(BaseModel):
    entity: str
    fact_type: str
    content: str
    valid_from: int
    valid_until: int | None
    importance: str


class BibleSnapshot(BaseModel):
    chapter: int
    facts: list[FactOut]
    entities_count: int


class ForeshadowOut(BaseModel):
    id: int
    description: str
    status: str
    chapter_planted: int
    expected_payoff_chapter: int | None
    payoff_chapter: int | None
    reinforcement_chapters: list
    importance: str
    is_due: bool

    model_config = {"from_attributes": True}


@router.get("/bible", response_model=BibleSnapshot)
async def bible_snapshot(
    project_id: int,
    chapter: int = Query(default=9999, description="查第几章时刻的状态,默认最新"),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    bible = BibleService(db, project_id)
    facts = bible.query_facts_at(chapter)
    ent_count = (
        db.query(Entity).filter(Entity.project_id == project_id).count()
    )
    return BibleSnapshot(
        chapter=chapter,
        entities_count=ent_count,
        facts=[
            FactOut(
                entity=bible._entity_name(f.entity_id),
                fact_type=f.fact_type,
                content=f.content,
                valid_from=f.valid_from,
                valid_until=f.valid_until,
                importance=f.importance,
            )
            for f in facts
        ],
    )


@router.get("/foreshadowings", response_model=list[ForeshadowOut])
async def list_foreshadowings(
    project_id: int,
    current_chapter: int = Query(default=9999, description="用于计算是否到期"),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    scheduler = ForeshadowScheduler(db, project_id)
    due_ids = {f.id for f in scheduler.due_foreshadowings(current_chapter)}
    rows = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .order_by(Foreshadowing.chapter_planted)
        .all()
    )
    out = []
    for f in rows:
        out.append(
            ForeshadowOut(
                id=f.id,
                description=f.description,
                status=f.status,
                chapter_planted=f.chapter_planted,
                expected_payoff_chapter=f.expected_payoff_chapter,
                payoff_chapter=f.payoff_chapter,
                reinforcement_chapters=f.reinforcement_chapters or [],
                importance=f.importance,
                is_due=f.id in due_ids,
            )
        )
    return out


# ---------- 人物管理闭环 ----------

# 关键事实排序:重要度优先(critical > major > normal > minor),同级按起始章
_IMP_ORDER = {"critical": 0, "major": 1, "normal": 2, "minor": 3}
_KEY_FACTS_LIMIT = 15


class CharacterFactOut(BaseModel):
    id: int
    fact_type: str
    content: str
    valid_from: int
    valid_until: int | None
    importance: str


class CharacterOut(BaseModel):
    id: int
    name: str
    aliases: list[str]
    entity_type: str
    retired: bool
    profile: str
    key_facts: list[CharacterFactOut]
    appearance_chapters: list[int]


class CharactersOut(BaseModel):
    characters: list[CharacterOut]
    other_entities_count: int


class CharacterCreate(BaseModel):
    name: str
    aliases: list[str] | None = None
    profile: str | None = None


class CharacterPatch(BaseModel):
    retired: bool


def _character_out(db: Session, project_id: int, ent: Entity, outlines: list[Outline]) -> CharacterOut:
    """组装单张人物卡:当前有效事实(前 N 条) + 出场章号并集。"""
    facts = (
        db.query(Fact)
        .filter(
            Fact.project_id == project_id,
            Fact.entity_id == ent.id,
            Fact.valid_until.is_(None),
        )
        .all()
    )
    facts.sort(key=lambda f: (_IMP_ORDER.get(f.importance, 1), f.valid_from))
    chapters = {f.source_chapter for f in facts if f.source_chapter > 0}
    # 事实覆盖不到的章节,用大纲的出场名单补齐(按实体名匹配)
    for o in outlines:
        if ent.name in (o.characters_involved or []):
            chapters.add(o.chapter_number)
    return CharacterOut(
        id=ent.id,
        name=ent.name,
        aliases=ent.aliases or [],
        entity_type=ent.entity_type,
        retired=bool(ent.retired),
        profile=(ent.base_profile or {}).get("profile", ""),
        key_facts=[
            CharacterFactOut(
                id=f.id,
                fact_type=f.fact_type,
                content=f.content,
                valid_from=f.valid_from,
                valid_until=f.valid_until,
                importance=f.importance,
            )
            for f in facts[:_KEY_FACTS_LIMIT]
        ],
        appearance_chapters=sorted(chapters),
    )


@router.get("/characters", response_model=CharactersOut)
async def list_characters(
    project_id: int,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    entities = (
        db.query(Entity)
        .filter(Entity.project_id == project_id)
        .order_by(Entity.id)
        .all()
    )
    outlines = (
        db.query(Outline).filter(Outline.project_id == project_id).all()
    )
    characters = [
        _character_out(db, project_id, e, outlines)
        for e in entities
        if e.entity_type == "character"
    ]
    return CharactersOut(
        characters=characters,
        other_entities_count=sum(
            1 for e in entities if e.entity_type != "character"
        ),
    )


@router.post("/characters", response_model=CharacterOut)
async def create_character(
    project_id: int,
    body: CharacterCreate,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="人物名字不能为空")
    aliases = [a.strip() for a in (body.aliases or []) if a.strip()]

    # 重名校验:名字或任一别名命中现有实体的名字/别名 → 400
    bible = BibleService(db, project_id)
    for candidate in [name, *aliases]:
        if bible.find_entity(candidate) is not None:
            raise HTTPException(
                status_code=400, detail=f"已存在同名(或别名)实体:{candidate}"
            )

    profile = (body.profile or "").strip()
    ent = Entity(
        project_id=project_id,
        entity_type="character",
        name=name,
        aliases=aliases,
        base_profile={"profile": profile} if profile else {},
        retired=False,
    )
    db.add(ent)
    db.flush()
    # 初始事实:让人物一登记就进入时序圣经,参与后续注入
    db.add(
        Fact(
            project_id=project_id,
            entity_id=ent.id,
            fact_type="state",
            content=profile or "初始登记",
            valid_from=1,
            valid_until=None,
            importance="normal",
            source_chapter=0,
        )
    )
    db.commit()
    db.refresh(ent)
    return _character_out(db, project_id, ent, [])


@router.patch("/characters/{entity_id}", response_model=CharacterOut)
async def patch_character(
    project_id: int,
    entity_id: int,
    body: CharacterPatch,
    db: Session = Depends(get_db),
):
    """退场/恢复。退场不删任何数据:历史正文与事实保留,
    只是后续章节生成不再注入该人物的状态约束。"""
    get_project_or_404(db, project_id)
    ent = db.get(Entity, entity_id)
    if ent is None or ent.project_id != project_id or ent.entity_type != "character":
        raise HTTPException(status_code=404, detail="人物不存在")
    ent.retired = body.retired
    db.commit()
    db.refresh(ent)
    outlines = (
        db.query(Outline).filter(Outline.project_id == project_id).all()
    )
    return _character_out(db, project_id, ent, outlines)


@router.delete("/facts/{fact_id}")
async def delete_fact(
    project_id: int,
    fact_id: int,
    db: Session = Depends(get_db),
):
    """删除单条事实(修正章后抽取抽错的内容),返回 ok。"""
    get_project_or_404(db, project_id)
    fact = db.get(Fact, fact_id)
    if fact is None or fact.project_id != project_id:
        raise HTTPException(status_code=404, detail="事实不存在")
    db.delete(fact)
    db.commit()
    return {"ok": True}
