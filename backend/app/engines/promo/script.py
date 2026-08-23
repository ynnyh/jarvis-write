# app/engines/promo/script.py
# -*- coding: utf-8 -*-
"""解说词:按锁定的创作简报写旁白脚本(素材点硬约束——史实/数据只可引用,不可编造)。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan
from app.engines.consistency.extractor import parse_llm_json
from app.engines.promo.assets import _brief_block
from app.engines.promo.common import clip
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_SCRIPT_PROMPT

_MAX_LINES = 24


class PromoScriptError(ValueError):
    """解说词生成的业务性错误(信息直接上屏)。"""


async def write_script(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    if not (plan.brief or {}).get("positioning"):
        raise PromoScriptError("先研讨并「收敛简报」——解说词按简报的契约执行。")

    progress("AI 正在按简报写解说词(事实只用素材点)…")
    adapter = get_adapter_for(Task.PROMO_SCRIPT, timeout=300)
    prompt = PROMO_SCRIPT_PROMPT.format(
        duration_s=plan.duration_s,
        subject=plan.subject.strip() or "(未定)",
        brief_block=_brief_block(plan),
        material_block=(plan.material_notes or "").strip() or "(无素材点——提醒:文案只能写意象,不得出现任何数字与史实)",
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    lines = []
    for item in (data.get("lines") or []):
        if not isinstance(item, dict):
            continue
        text = clip(item.get("text"), 200)
        if not text:
            continue
        lines.append(
            {
                "speaker": clip(item.get("speaker"), 60) or "旁白",
                "text": text,
                "action": clip(item.get("action"), 120),
            }
        )
        if len(lines) >= _MAX_LINES:
            break
    if not lines:
        raise PromoScriptError("解说词结果为空,请重试。")

    plan.script = {"synopsis": clip(data.get("synopsis"), 300), "lines": lines}
    plan.status = "scripted"
    db.commit()
    return {"script": plan.script}
