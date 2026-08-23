# app/engines/promo/assets.py
# -*- coding: utf-8 -*-
"""宣传片资产:风格卡(方向硬约束)+ 地标卡(场景锚)。一条企划各一套,存 plan 内嵌字段。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import clip, direction_directive, direction_label
from app.engines.promo.common import angles_block
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_LANDMARK_PROMPT, PROMO_STYLE_PROMPT


class PromoAssetError(ValueError):
    """资产生成的业务性错误(信息直接上屏)。"""


async def generate_style(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    """生成(覆盖式)风格卡字段:方向是硬约束。"""
    if not plan.subject.strip():
        raise PromoAssetError("先填「主题」(城市/景区/品牌),再定视觉风格。")
    direction = plan.direction or "live"
    progress(f"AI 正在定视觉风格(方向:{direction_label(direction)})…")
    adapter = get_adapter_for(Task.PROMO_ASSET, timeout=300)
    tone = "、".join((plan.brief or {}).get("tone") or []) or "(简报未定,按主题气质)"
    prompt = PROMO_STYLE_PROMPT.format(
        duration_s=plan.duration_s,
        subject=plan.subject.strip(),
        angles_block=angles_block(plan.angles),
        tone=tone,
        direction_directive=direction_directive(direction),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    plan.style_name = clip(data.get("style_name"), 60)
    plan.style_cn = clip(data.get("style_cn"), 400)
    plan.style_en = clip(data.get("style_en"), 400)
    plan.negative = clip(data.get("negative"), 300)
    db.commit()
    return {"style_name": plan.style_name, "style_cn": plan.style_cn,
            "style_en": plan.style_en, "negative": plan.negative}


def _brief_block(plan: PromoPlan) -> str:
    brief = plan.brief or {}
    if not brief.get("positioning"):
        return "(简报未收敛——先研讨并收敛简报)"
    segs = [
        f"- {s.get('title')}({s.get('angle')}/{s.get('seconds')}s):{s.get('beat')}"
        for s in brief.get("structure") or []
        if isinstance(s, dict)
    ]
    return "\n".join(
        [
            f"定位:{brief.get('positioning')}",
            f"受众:{brief.get('audience')}",
            f"基调:{'、'.join(brief.get('tone') or [])}",
            f"核心信息:{';'.join(brief.get('key_messages') or [])}",
            "段落:",
            *(segs or ["(无)"]),
        ]
    )


async def generate_landmarks(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    """按简报生成地标卡(场景锚,覆盖式)。"""
    if not (plan.brief or {}).get("positioning"):
        raise PromoAssetError("先研讨并「收敛简报」,地标卡跟着简报的段落走。")
    progress("AI 正在为出镜场景定调(地标卡)…")
    adapter = get_adapter_for(Task.PROMO_ASSET, timeout=300)
    prompt = PROMO_LANDMARK_PROMPT.format(
        subject=plan.subject.strip(),
        brief_block=_brief_block(plan),
        material_block=(plan.material_notes or "").strip() or "(无)",
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    out = []
    for item in (data.get("landmarks") or []):
        if not isinstance(item, dict):
            continue
        name = clip(item.get("name"), 200)
        if not name:
            continue
        out.append(
            {
                "name": name,
                "appearance_cn": clip(item.get("appearance_cn"), 400),
                "appearance_en": clip(item.get("appearance_en"), 300),
            }
        )
        if len(out) >= 8:
            break
    if not out:
        raise PromoAssetError("地标卡结果为空,请重试。")
    plan.landmarks = out
    db.commit()
    return {"landmarks": out}
