# app/engines/promo/prompt_render.py
# -*- coding: utf-8 -*-
"""宣传片三轨提示词:画风锚 + 地标锚逐字注入每一格,LLM 漏注入时引擎兜底(同漫剧纪律)。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.anchors import ensure_style_anchors, merge_negative
from app.engines.media.text import clip
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_SHOT_PROMPT_PROMPT

_CHUNK = 8


class PromoPromptError(ValueError):
    """提示词渲染的业务性错误(信息直接上屏)。"""


def _landmark_anchor_block(shots: list[PromoShot], landmarks: list[dict]) -> str:
    names = {s.scene_name.strip() for s in shots if s.scene_name.strip()}
    by_name = {str(l.get("name")): l for l in landmarks if isinstance(l, dict) and l.get("name")}
    lines = [
        f"【{by_name[n]['name']}】{by_name[n].get('appearance_cn')}\n  EN: {by_name[n].get('appearance_en')}"
        for n in names
        if n in by_name
    ]
    return "\n".join(lines) or "(本块镜头无地标卡命中,按场景名自行合理设计环境)"


def _shots_block(shots: list[PromoShot]) -> str:
    rows = []
    for s in shots:
        rows.append(
            f"- seq {s.seq}|{s.shot_type}/{s.camera}/{s.duration_s}s"
            f"|场景:{s.scene_name or '(未指定)'}\n"
            f"  画面:{s.action_desc}\n"
            f"  解说词:{s.dialogue or '(无)'}"
        )
    return "\n".join(rows)


async def render_shot_prompts(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    if not plan.style_cn and not plan.style_en:
        raise PromoPromptError("先生成「视觉风格」,全片画风统一靠它。")
    shots = (
        db.query(PromoShot).filter(PromoShot.promo_id == plan.id).order_by(PromoShot.seq).all()
    )
    if not shots:
        raise PromoPromptError("还没有分镜,先「拆分镜」再出提示词。")

    adapter = get_adapter_for(Task.PROMO_PROMPT, timeout=300)
    total = len(shots)
    for start in range(0, total, _CHUNK):
        chunk = shots[start : start + _CHUNK]
        progress(f"AI 正在出提示词({start + 1}-{min(start + _CHUNK, total)}/{total} 格)…")
        prompt = PROMO_SHOT_PROMPT_PROMPT.format(
            style_cn=plan.style_cn,
            style_en=plan.style_en,
            style_negative=plan.negative,
            landmark_anchors=_landmark_anchor_block(chunk, plan.landmarks or []),
            shots_block=_shots_block(chunk),
        )
        raw = await adapter.ask(prompt)
        data = parse_llm_json(raw)
        by_seq: dict[int, dict] = {}
        for item in (data.get("shots") or []):
            if isinstance(item, dict) and item.get("seq") is not None:
                try:
                    by_seq[int(item["seq"])] = item
                except (TypeError, ValueError):
                    continue
        for shot in chunk:
            item = by_seq.get(shot.seq) or {}
            prompt_cn = clip(item.get("prompt_cn"), 1200)
            prompt_en = clip(item.get("prompt_en"), 800)
            negative = clip(item.get("negative"), 500)
            if not prompt_cn and not prompt_en:
                continue
            # 兜底注入:画风锚(中英)与负面基座,与漫剧同纪律(负面词合并只走 media.anchors 一处)
            prompt_cn, prompt_en = ensure_style_anchors(prompt_cn, prompt_en, plan.style_cn, plan.style_en)
            negative = merge_negative(negative, plan.negative)
            shot.prompt_cn = prompt_cn
            shot.prompt_en = prompt_en
            shot.negative = negative

    from app.engines.promo.common import shot_dict as _sd

    if all(s.prompt_cn or s.prompt_en for s in shots):
        plan.status = "ready"
    db.commit()
    shots = (
        db.query(PromoShot).filter(PromoShot.promo_id == plan.id).order_by(PromoShot.seq).all()
    )
    return {"shots": [_sd(s) for s in shots]}
