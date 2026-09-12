# app/api/projects/plot_map.py
# -*- coding: utf-8 -*-
"""情节推进图(docs/19 M5):章×场景网格 + 伏笔埋收全链,一屏理顺情节进展。

**纯确定性投影,零抽取**:场景来自 Scene 卡(切分过的书;未切分回落大纲 beats),
张力/情绪着色、梗兑现拍、伏笔的埋设章→回收/预期章全链。前端画网格与伏笔连线,
本端点只管把账摆齐——缺数据如实缺省。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Foreshadowing, Outline, Scene, User
from app.db.session import get_db

from ._common import _get_project_or_404

router = APIRouter()


class MapScene(BaseModel):
    seq: int
    title: str
    location: str
    emotion_target: str
    tension_level: int
    status: str


class MapChapter(BaseModel):
    chapter_number: int
    title: str
    chapter_role: str
    emotional_tone: str
    premise_beat: str
    written: bool
    scenes: list[MapScene] = []
    beats: list[str] = []


class MapForeshadow(BaseModel):
    id: int
    description: str
    status: str
    planted: int
    payoff: int | None = None
    expected: int | None = None


class PlotMapOut(BaseModel):
    chapters: list[MapChapter] = []
    foreshadows: list[MapForeshadow] = []


@router.get("/{project_id}/plot-map", response_model=PlotMapOut)
def plot_map(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """情节推进图数据:全蓝图的章(含场景卡与梗兑现拍)+ 伏笔埋收链。"""
    project = _get_project_or_404(db, project_id)
    outlines = (
        db.query(Outline)
        .filter(Outline.project_id == project.id)
        .order_by(Outline.chapter_number)
        .all()
    )

    from app.db.models import Chapter

    written_chapters = {
        c.chapter_number
        for c in db.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .all()
        if (c.final_content or "").strip()
    }

    chapters: list[MapChapter] = []
    for o in outlines:
        scenes = (
            db.query(Scene)
            .filter(Scene.outline_id == o.id)
            .order_by(Scene.seq)
            .all()
        )
        chapters.append(
            MapChapter(
                chapter_number=o.chapter_number,
                title=o.title,
                chapter_role=o.chapter_role,
                emotional_tone=o.emotional_tone,
                premise_beat=o.premise_beat or "",
                written=o.chapter_number in written_chapters,
                scenes=[
                    MapScene(
                        seq=s.seq, title=s.title, location=s.location,
                        emotion_target=s.emotion_target,
                        tension_level=s.tension_level, status=s.status,
                    )
                    for s in scenes
                ],
                beats=[str(b) for b in (o.beats or [])],
            )
        )

    fors = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project.id)
        .order_by(Foreshadowing.chapter_planted)
        .all()
    )
    foreshadows = [
        MapForeshadow(
            id=f.id, description=f.description, status=f.status,
            planted=f.chapter_planted, payoff=f.payoff_chapter,
            expected=f.expected_payoff_chapter,
        )
        for f in fors
    ]
    return PlotMapOut(chapters=chapters, foreshadows=foreshadows)
