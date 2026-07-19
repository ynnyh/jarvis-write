# app/api/consistency.py
# -*- coding: utf-8 -*-
"""一致性看板接口:故事圣经与伏笔状态的查看。

GET /api/projects/{id}/bible?chapter=N     第 N 章时刻的实体状态快照(时序查询)
GET /api/projects/{id}/foreshadowings      伏笔清单(四态 + 到期提醒)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, get_current_user
from app.db.models import Entity, Foreshadowing, Project
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


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(p)
    return p


@router.get("/bible", response_model=BibleSnapshot)
async def bible_snapshot(
    project_id: int,
    chapter: int = Query(default=9999, description="查第几章时刻的状态,默认最新"),
    db: Session = Depends(get_db),
):
    _project(db, project_id)
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
    _project(db, project_id)
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
