# app/engines/drama/voice.py
# -*- coding: utf-8 -*-
"""声线选型卡:给角色卡补 TTS 平台选型建议与朗读指示(阶段 2 配音环节的入口)。

locked 的卡跳过(与角色卡批量生成同一语义:锁定即冻结整卡)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaCharacterCard, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import character_card_dict, clip
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import VOICE_CAST_PROMPT

_MAX_CHARACTERS = 12


class DramaVoiceError(ValueError):
    """声线选型的业务性错误(信息直接上屏)。"""


async def generate_voice_cast(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """为全部角色卡(locked 除外)批量补 tts_hint / reading_notes。"""
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .order_by(DramaCharacterCard.id)
        .limit(_MAX_CHARACTERS)
        .all()
    )
    if not cards:
        raise DramaVoiceError("还没有角色卡——先在上方「AI 生成资产卡」。")

    unlocked = [c for c in cards if not c.locked]
    if not unlocked:
        return {"cards": [character_card_dict(c) for c in cards], "skipped_locked": len(cards)}

    lines = [
        f"【{c.name}】声线:{c.voice_desc or '未定'}|身份线索:{c.outfit_cn or ''}"
        for c in unlocked
    ]
    progress(f"AI 正在为 {len(unlocked)} 个角色配声线选型…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = VOICE_CAST_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        cards_block="\n".join(lines),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    by_name = {c.name: c for c in unlocked}
    for item in (data.get("casts") or []):
        if not isinstance(item, dict):
            continue
        name = clip(item.get("name"), 200)
        target = by_name.get(name)
        if target is None:
            continue
        target.tts_hint = clip(item.get("tts_hint"), 300)
        target.reading_notes = clip(item.get("reading_notes"), 200)

    db.commit()
    return {
        "cards": [character_card_dict(c) for c in cards],
        "skipped_locked": len(cards) - len(unlocked),
    }
