# app/engines/drama/style.py
# -*- coding: utf-8 -*-
"""美术风格卡:项目级画风锁定段,注入每条分镜提示词(全片统一,借鉴 LumenX 可控美术指导)。

画风方向(direction)由用户显式拍板(动画系默认推荐,真人写实挂警示),
方向目录见 common.DRAMA_DIRECTIONS;recommend_directions 按书的内容荐前三个方向
(带理由与优先级),AI 荐、用户选,荐完仍可无视。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaStyleCard, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    DRAMA_DIRECTIONS,
    VALID_DIRECTIONS,
    clip,
    concept_block,
    direction_directive,
    direction_label,
    style_card_dict,
    style_memo_block,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import DIRECTION_RECOMMEND_PROMPT, STYLE_PROMPT


class DramaStyleError(ValueError):
    """风格卡生成的业务性错误(信息直接上屏)。"""


async def generate_style_card(
    db: Session, project: Project, direction: str = "auto", progress=lambda s: None
) -> dict:
    """生成(或重新生成,覆盖式)项目美术风格卡;direction 为画风方向硬约束。"""
    if direction not in VALID_DIRECTIONS:
        raise DramaStyleError(f"未知画风方向:{direction}")
    progress(
        f"AI 正在定全片美术风格(方向:{direction_label(direction)})…"
        if direction != "auto"
        else "AI 正在定全片美术风格…"
    )
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = STYLE_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic.strip() or "(未定)",
        concept_block=concept_block(project),
        style_memo_block=style_memo_block(project),
        direction_directive=direction_directive(direction),
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
    card.direction = direction
    card.style_name = clip(data.get("style_name"), 60)
    card.style_cn = clip(data.get("style_cn"), 400)
    card.style_en = clip(data.get("style_en"), 400)
    card.negative = clip(data.get("negative"), 300)
    db.commit()
    return style_card_dict(card)


async def recommend_directions(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """按书的题材/基调/场景推荐前 3 个画风方向(带理由,按优先级排序)。"""
    progress("AI 正在按本书气质推荐画风方向…")
    directions_block = "\n".join(
        f"{d['key']} | {d['label']} | {d['directive']}" for d in DRAMA_DIRECTIONS
    )
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = DIRECTION_RECOMMEND_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic.strip() or "(未定)",
        concept_block=concept_block(project),
        style_memo_block=style_memo_block(project),
        directions_block=directions_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    by_key = {d["key"]: d for d in DRAMA_DIRECTIONS}
    out: list[dict] = []
    seen: set[str] = set()
    for item in (data.get("recommendations") or []):
        if not isinstance(item, dict):
            continue
        key = clip(item.get("key"), 40)
        if key not in by_key or key in seen or key == "auto":
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "label": by_key[key]["label"],
                "tip": by_key[key]["tip"],
                "reason": clip(item.get("reason"), 300),
                "priority": len(out) + 1,
            }
        )
        if len(out) >= 3:
            break
    if not out:
        raise DramaStyleError("方向推荐结果为空,请重试。")
    return {"recommendations": out}
