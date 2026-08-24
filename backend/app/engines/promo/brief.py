# app/engines/promo/brief.py
# -*- coding: utf-8 -*-
"""简报收敛:把研讨记录蒸馏成结构化「创作简报」——后续解说词与分镜的契约。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.text import coerce_int
from app.engines.promo.common import angles_block, direction_block
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_BRIEF_PROMPT

_MAX_TURNS = 20
_MAX_CHAT_CHARS = 6000


class PromoBriefError(ValueError):
    """简报收敛的业务性错误(信息直接上屏)。"""


def _chat_block(plan: PromoPlan) -> str:
    turns = [
        f"{'客户' if m.get('role') == 'user' else '总监'}:{str(m.get('text') or m.get('content') or '').strip()}"
        for m in (plan.chat_log or [])
        if isinstance(m, dict) and str(m.get("text") or m.get("content") or "").strip()
    ]
    if not turns:
        return ""
    return "\n".join(turns[-_MAX_TURNS:])[:_MAX_CHAT_CHARS]


def _clips(lst, n: int, width: int) -> list[str]:
    out = []
    for x in lst or []:
        s = str(x or "").strip()
        if s:
            out.append(s[:width])
        if len(out) >= n:
            break
    return out


def normalize_brief(data: dict, duration_s: int) -> dict:
    """裁剪简报输出:字段限长、段落 3-5 个、seconds 收敛非负。"""
    structure = []
    for item in (data.get("structure") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:60]
        if not title:
            continue
        structure.append(
            {
                "title": title,
                "angle": str(item.get("angle") or "").strip()[:60],
                "beat": str(item.get("beat") or "").strip()[:200],
                "seconds": coerce_int(item.get("seconds"), 20, lo=3, hi=120),
            }
        )
        if len(structure) >= 5:
            break
    tone_raw = str(data.get("tone") or "").replace(",", " ").replace("、", " ").replace("/", " ")
    return {
        "positioning": str(data.get("positioning") or "").strip()[:120],
        "audience": str(data.get("audience") or "").strip()[:200],
        "tone": _clips(tone_raw.split(), 4, 20),
        "key_messages": _clips(data.get("key_messages"), 4, 120),
        "structure": structure,
        "slogan_candidates": _clips(data.get("slogan_candidates"), 3, 60),
        "cautions": _clips(data.get("cautions"), 6, 200),
        "duration_s": duration_s,
    }


async def distill_brief(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    """研讨记录 → 结构化简报(覆盖式;研讨没聊过会直接报错引导先聊)。"""
    chat = _chat_block(plan)
    if not chat and not (plan.material_notes or "").strip():
        raise PromoBriefError("还没有研讨记录——先和 AI 把方向聊出来,或至少填一些素材点。")

    progress("AI 正在把研讨共识收敛成创作简报…")
    adapter = get_adapter_for(Task.PROMO_BRIEF, timeout=300)
    prompt = PROMO_BRIEF_PROMPT.format(
        subject=plan.subject.strip() or "(未定)",
        angles_block=angles_block(plan.angles),
        duration_s=plan.duration_s,
        direction_block=direction_block(plan.direction or "live"),
        material_block=(plan.material_notes or "").strip() or "(无)",
        chat_block=chat or "(无研讨记录,按表单信息收敛)",
    )
    raw = await adapter.ask(prompt)
    brief = normalize_brief(parse_llm_json(raw), plan.duration_s)
    if not brief["positioning"] or not brief["structure"]:
        raise PromoBriefError("简报收敛结果不完整,请重试(或先补充研讨)。")

    plan.brief = brief
    plan.brief_locked = False
    if plan.status == "draft":
        plan.status = "briefed"
    db.commit()
    return {"brief": brief}
