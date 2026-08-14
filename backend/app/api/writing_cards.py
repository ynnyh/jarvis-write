# app/api/writing_cards.py
# -*- coding: utf-8 -*-
"""写作手法卡接口:每本书自己的手法库,勾选启用即注入所有生成节点。

GET    /api/projects/{id}/cards            列出本书全部手法卡(含未启用)
POST   /api/projects/{id}/cards            新建一张卡(sort 默认排到末尾)
PATCH  /api/projects/{id}/cards/{card_id}  改名 / 改正文 / 启停 / 调序
DELETE /api/projects/{id}/cards/{card_id}  删卡

启用的卡由 engines/tendency/cards.py 渲染成【写作手法卡】块,追加到 style_block,
故润色 / 正文草稿与定稿 / 重写全部生效(见该模块注释)。正文长度上限与渲染层
的截断阈值保持一致,避免"存进去了但注入时被砍"的错觉。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import WritingCard
from app.db.session import get_db
from app.engines.tendency.cards import MAX_BODY_CHARS, MAX_CARDS, render_cards_block

logger = logging.getLogger("jarvis-write.cards")

router = APIRouter(
    prefix="/api/projects/{project_id}/cards",
    tags=["writing-cards"],
    dependencies=[Depends(get_current_user)],
)

_MAX_CARDS_PER_PROJECT = 50  # 库容上限(启用数另由渲染层限 MAX_CARDS)


class CardOut(BaseModel):
    id: int
    title: str
    body: str
    enabled: bool
    sort: int


class CardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    enabled: bool = False


class CardUpdate(BaseModel):
    """全字段可选:前端切开关只传 enabled,不必回传整卡。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY_CHARS)
    enabled: bool | None = None
    sort: int | None = Field(default=None, ge=0, le=9999)


def _card_or_404(db: Session, project_id: int, card_id: int) -> WritingCard:
    """取卡并校验归属:别人书里的卡 id 一律当不存在(不泄漏存在性)。"""
    card = db.get(WritingCard, card_id)
    if card is None or card.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"手法卡 {card_id} 不存在")
    return card


def _out(card: WritingCard) -> CardOut:
    return CardOut(
        id=card.id,
        title=card.title,
        body=card.body,
        enabled=bool(card.enabled),
        sort=card.sort or 0,
    )


@router.get("", response_model=list[CardOut])
async def list_cards(project_id: int, db: Session = Depends(get_db)):
    """列出本书全部手法卡,按 sort 升序(与注入顺序一致),同 sort 按 id。"""
    get_project_or_404(db, project_id)
    cards = (
        db.query(WritingCard)
        .filter(WritingCard.project_id == project_id)
        .order_by(WritingCard.sort, WritingCard.id)
        .all()
    )
    return [_out(c) for c in cards]


@router.post("", response_model=CardOut, status_code=201)
async def create_card(project_id: int, req: CardCreate, db: Session = Depends(get_db)):
    """新建手法卡:sort 自动排到当前末尾(+10 留出手动插空间)。"""
    get_project_or_404(db, project_id)
    count = (
        db.query(func.count(WritingCard.id))
        .filter(WritingCard.project_id == project_id)
        .scalar()
        or 0
    )
    if count >= _MAX_CARDS_PER_PROJECT:
        raise HTTPException(
            status_code=400,
            detail=f"单本书最多 {_MAX_CARDS_PER_PROJECT} 张手法卡,请先清理",
        )
    max_sort = (
        db.query(func.max(WritingCard.sort))
        .filter(WritingCard.project_id == project_id)
        .scalar()
        or 0
    )
    card = WritingCard(
        project_id=project_id,
        title=req.title.strip(),
        body=req.body.strip(),
        enabled=req.enabled,
        sort=max_sort + 10,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    logger.info("新建手法卡: 项目%d 卡%d《%s》", project_id, card.id, card.title)
    return _out(card)


@router.patch("/{card_id}", response_model=CardOut)
async def update_card(
    project_id: int, card_id: int, req: CardUpdate, db: Session = Depends(get_db)
):
    """局部更新:只改传了的字段(未传的保持原值)。"""
    get_project_or_404(db, project_id)
    card = _card_or_404(db, project_id, card_id)
    data = req.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        card.title = data["title"].strip()
    if "body" in data and data["body"] is not None:
        card.body = data["body"].strip()
    if "enabled" in data and data["enabled"] is not None:
        card.enabled = data["enabled"]
    if "sort" in data and data["sort"] is not None:
        card.sort = data["sort"]
    db.commit()
    db.refresh(card)
    return _out(card)


@router.delete("/{card_id}")
async def delete_card(project_id: int, card_id: int, db: Session = Depends(get_db)):
    """删卡(不做软删:手法卡本身是可随时重建的轻量文本)。"""
    get_project_or_404(db, project_id)
    card = _card_or_404(db, project_id, card_id)
    db.delete(card)
    db.commit()
    logger.info("删除手法卡: 项目%d 卡%d", project_id, card_id)
    return {"status": "deleted", "id": card_id}


@router.get("/preview", response_model=dict)
async def preview_block(project_id: int, db: Session = Depends(get_db)):
    """预览当前启用卡拼出的注入块原文(让作者看到"AI 究竟读到了什么")。"""
    get_project_or_404(db, project_id)
    cards = (
        db.query(WritingCard)
        .filter(WritingCard.project_id == project_id)
        .order_by(WritingCard.sort, WritingCard.id)
        .all()
    )
    enabled = [c for c in cards if c.enabled and (c.body or "").strip()]
    return {
        "block": render_cards_block(cards),
        "enabled_count": len(enabled),
        "max_inject": MAX_CARDS,
    }
