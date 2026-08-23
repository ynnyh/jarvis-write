# app/engines/promo/storyboard.py
# -*- coding: utf-8 -*-
"""宣传片分镜:解说词 → 镜头清单(场景关联地标卡,覆盖式重生成)。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import coerce_int
from app.engines.promo.common import shot_dict
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_STORYBOARD_PROMPT

_MAX_SHOTS = 24


class PromoStoryboardError(ValueError):
    """分镜的业务性错误(信息直接上屏)。"""


async def build_storyboard(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    script = plan.script or {}
    lines = script.get("lines") if isinstance(script, dict) else None
    if not lines:
        raise PromoStoryboardError("还没有解说词,先「写解说词」再拆分镜。")

    landmark_names = [str(l.get("name")) for l in (plan.landmarks or []) if isinstance(l, dict) and l.get("name")]
    lines_block = "\n".join(
        f"  {i}. {l.get('text', '')}" + (f"  (画面:{l.get('action')})" if l.get("action") else "")
        for i, l in enumerate(lines, start=1)
        if isinstance(l, dict)
    )
    estimate = max(6, round(plan.duration_s / 4))
    progress(f"AI 正在拆分镜(预计 {estimate} 格左右,覆盖旧分镜)…")

    adapter = get_adapter_for(Task.PROMO_STORYBOARD, timeout=300)
    prompt = PROMO_STORYBOARD_PROMPT.format(
        duration_s=plan.duration_s,
        landmark_names="、".join(landmark_names) or "(暂无地标卡,按解说词 action 自拟简短场景名)",
        lines_block=lines_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    db.query(PromoShot).filter(PromoShot.promo_id == plan.id).delete(
        synchronize_session=False
    )
    count = 0
    for item in (data.get("shots") or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        count += 1
        db.add(
            PromoShot(
                promo_id=plan.id,
                seq=count,
                scene_name=str(item.get("scene_name") or "").strip()[:200],
                characters=[str(c).strip() for c in (item.get("characters") or []) if str(c or "").strip()][:4],
                action_desc=action[:300],
                shot_type=str(item.get("shot_type") or "").strip()[:20],
                camera=str(item.get("camera") or "").strip()[:20],
                dialogue=str(item.get("dialogue") or "").strip()[:400],
                duration_s=coerce_int(item.get("duration_s"), 4, lo=1, hi=10),
            )
        )
        if count >= _MAX_SHOTS:
            break
    if count == 0:
        raise PromoStoryboardError("分镜结果为空,请重试(或先重写解说词)。")
    plan.status = "storyboarded"
    db.commit()

    shots = (
        db.query(PromoShot).filter(PromoShot.promo_id == plan.id).order_by(PromoShot.seq).all()
    )
    return {"shots": [shot_dict(s) for s in shots]}
