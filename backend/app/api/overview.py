# app/api/overview.py
# -*- coding: utf-8 -*-
"""全书概览聚合接口:看板「概览」页签一次拿齐三块数据。

GET /api/projects/{id}/overview
- chapters: 大纲 × 正文的逐章状态/版本对照(章节网格地图)
- foreshadowings: 四态 + 埋设/预期/回收章(伏笔时间线)
- characters: 每个人物的出场章号列表(人物出场时间线)

只读聚合:数据全部来自现有表(outlines/chapters/foreshadowings/entities/facts),
不加新表,不改任何写路径。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.consistency import _appearance_chapters
from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, Entity, Fact, Foreshadowing, Outline
from app.db.session import get_db

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["overview"],
    dependencies=[Depends(get_current_user)],
)


class OverviewChapter(BaseModel):
    chapter_number: int
    title: str
    chapter_role: str
    # 无正文行时为 empty;生成中为 drafting(前端蓝色脉动)
    status: str
    word_count: int
    is_stale: bool
    # 正文基于的大纲版本;未生成为 None
    outline_version_used: int | None
    outline_current_version: int
    characters_involved: list[str]


class OverviewForeshadow(BaseModel):
    content: str
    status: str
    planted_chapter: int
    expected_chapter: int | None
    resolved_chapter: int | None


class OverviewCharacter(BaseModel):
    name: str
    retired: bool
    chapters: list[int]


class OverviewOut(BaseModel):
    chapters: list[OverviewChapter]
    foreshadowings: list[OverviewForeshadow]
    characters: list[OverviewCharacter]


@router.get("/overview", response_model=OverviewOut)
async def overview(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)

    outlines = (
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
        .all()
    )
    ch_by_num = {
        c.chapter_number: c
        for c in db.query(Chapter).filter(Chapter.project_id == project_id)
    }

    entities = (
        db.query(Entity)
        .filter(
            Entity.project_id == project_id,
            Entity.entity_type == "character",
        )
        .order_by(Entity.id)
        .all()
    )
    # 当前有效事实一次查出,按实体分组(避免逐人物 N+1)
    facts_by_entity: dict[int, list[Fact]] = {}
    for f in (
        db.query(Fact)
        .filter(Fact.project_id == project_id, Fact.valid_until.is_(None))
        .all()
    ):
        facts_by_entity.setdefault(f.entity_id, []).append(f)

    return OverviewOut(
        chapters=[
            OverviewChapter(
                chapter_number=o.chapter_number,
                title=o.title,
                chapter_role=o.chapter_role,
                status=(ch.status if ch else "empty"),
                word_count=(ch.word_count if ch else 0),
                is_stale=bool(ch.is_stale) if ch else False,
                outline_version_used=(ch.outline_version_used if ch else None),
                outline_current_version=o.current_version,
                characters_involved=list(o.characters_involved or []),
            )
            for o in outlines
            for ch in [ch_by_num.get(o.chapter_number)]
        ],
        foreshadowings=[
            OverviewForeshadow(
                content=f.description,
                status=f.status,
                planted_chapter=f.chapter_planted,
                expected_chapter=f.expected_payoff_chapter,
                resolved_chapter=f.payoff_chapter,
            )
            for f in db.query(Foreshadowing)
            .filter(Foreshadowing.project_id == project_id)
            .order_by(Foreshadowing.chapter_planted)
        ],
        characters=[
            OverviewCharacter(
                name=e.name,
                retired=bool(e.retired),
                chapters=_appearance_chapters(
                    facts_by_entity.get(e.id, []), e, outlines
                ),
            )
            for e in entities
        ],
    )
