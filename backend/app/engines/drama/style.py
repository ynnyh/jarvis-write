# app/engines/drama/style.py
# -*- coding: utf-8 -*-
"""美术风格卡:项目级画风锁定段,注入每条分镜提示词(全片统一,借鉴 LumenX 可控美术指导)。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaStyleCard, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import clip, concept_block, style_card_dict, style_memo_block
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import STYLE_PROMPT


async def generate_style_card(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """生成(或重新生成,覆盖式)项目美术风格卡。"""
    progress("AI 正在定全片美术风格…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = STYLE_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic.strip() or "(未定)",
        concept_block=concept_block(project),
        style_memo_block=style_memo_block(project),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    card = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project.id)
        .first()
    )
    if card is None:
        card = DramaStyleCard(project_id=project.id)
        db.add(card)
    card.style_name = clip(data.get("style_name"), 60)
    card.style_cn = clip(data.get("style_cn"), 400)
    card.style_en = clip(data.get("style_en"), 400)
    card.negative = clip(data.get("negative"), 300)
    db.commit()
    return style_card_dict(card)
