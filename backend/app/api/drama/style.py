# app/api/drama/style.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 美术风格卡路由。

拆分自原 app/api/drama.py。包含风格卡的读取、保存、生成和方向推荐。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.drama._common import (
    StyleCardIn,
    StyleGenIn,
    _existing_job,
    generate_style_card,
    get_db,
    get_project_or_404,
    make_sub_router,
    recommend_directions,
    style_card_dict,
    VALID_DIRECTIONS,
    clip,
    DramaStyleCard,
    Project,
    spawn_job,
)

router = make_sub_router()


# =============== 美术风格卡 ===============


@router.get("/style")
async def get_style(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    card = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    return {"style": style_card_dict(card)}


@router.put("/style")
async def save_style(project_id: int, body: StyleCardIn, db: Session = Depends(get_db)):
    """手动保存风格卡(没有则建)。"""
    get_project_or_404(db, project_id)
    card = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    if card is None:
        card = DramaStyleCard(project_id=project_id)
        db.add(card)
    if body.direction is not None:
        if body.direction not in VALID_DIRECTIONS:
            raise HTTPException(status_code=400, detail=f"未知画风方向:{body.direction}")
        card.direction = body.direction
    card.style_name = clip(body.style_name, 60)
    card.style_cn = clip(body.style_cn, 400)
    card.style_en = clip(body.style_en, 400)
    card.negative = clip(body.negative, 300)
    card.ratio = clip(body.ratio, 10) or "9:16"
    db.commit()
    return {"style": style_card_dict(card)}


@router.post("/style/generate")
async def generate_style(
    project_id: int, body: StyleGenIn | None = None, db: Session = Depends(get_db)
):
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再定美术风格。")
    direction = (body.direction if body else "") or "auto"
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"未知画风方向:{direction}")
    kind = f"drama-style-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_style_card(session, proj, direction, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/style/recommend-directions")
async def recommend_directions_ep(project_id: int, db: Session = Depends(get_db)):
    """按书的题材/基调推荐前 3 个画风方向(带理由,按优先级排序);AI 荐,用户选。"""
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再推荐方向。")
    kind = f"drama-dirrec-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await recommend_directions(session, proj, progress)

    return {"job_id": spawn_job(kind, work)}
