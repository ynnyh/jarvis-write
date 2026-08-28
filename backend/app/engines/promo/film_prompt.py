# app/engines/promo/film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(宣传片):分镜 + 解说词 + 地标卡 + 风格卡 → 一条出一整片。

宣传片以解说词驱动的空镜画面为主,没有人物卡——一致性段走「画面一致性」
(画风/色调/全片统一约定),素材点(material_notes)是解说与画面的硬约束。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.media.text import speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import (
    FILM_PROMPT_TEMPLATE,
    PROMO_FRAMING,
    SOUND_RULE_PROMO,
)


class PromoFilmPromptError(ValueError):
    """整片提示词生成的业务性错误(信息直接上屏)。"""


async def build_promo_film_prompt(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    """组装生成整片提示词,整体覆盖 plan.film_prompt。返回字数供 job 结果展示。"""
    shots = (
        db.query(PromoShot)
        .filter(PromoShot.promo_id == plan.id)
        .order_by(PromoShot.seq)
        .all()
    )
    if not shots:
        raise PromoFilmPromptError(
            "这条宣传片还没有分镜:先「生成分镜」,再来生成整片提示词。"
        )
    script = plan.script or {}
    lines = script.get("lines") or []

    rows = []
    total_s = 0
    for s in shots:
        total_s += int(s.duration_s or 0)
        dialogue = (s.dialogue or "").strip()
        speaker = speaker_of(dialogue, lines)
        row = (
            f"- 镜头{s.seq}|{s.shot_type or '空镜'}"
            f"|运镜:{s.camera or '固定'}|{s.duration_s}秒"
            f"|场景:{s.scene_name or '未标'}"
            f"|画面:{(s.action_desc or '').strip() or '未写'}"
        )
        if dialogue:
            row += f"|解说:{f'{speaker}:' if speaker else ''}{dialogue}"
        rows.append(row)

    landmarks = [
        f"- {str(l.get('name') or '').strip()}:{str(l.get('appearance_cn') or '').strip()}"
        for l in (plan.landmarks or [])
        if isinstance(l, dict) and str(l.get('name') or '').strip()
    ]
    extra = ""
    if landmarks:
        extra += "【地标卡(场景锚,镜头表的场景描述要贴着写)】\n" + "\n".join(landmarks) + "\n"
    if (plan.material_notes or "").strip():
        extra += f"【素材点(卖点/事实,解说与画面的硬约束)】{plan.material_notes.strip()}\n"

    progress("AI 正在把分镜组装成整片提示词…")
    adapter = get_adapter_for(Task.PROMO_PROMPT, timeout=300)
    prompt = FILM_PROMPT_TEMPLATE.format(
        workshop_label="宣传片企划",
        title_line=f"{plan.subject or '未填主体'} · {plan.title or '未命名'}",
        ratio_total=f"9:16 竖屏,{total_s or int(plan.duration_s or 60)} 秒左右",
        framing=PROMO_FRAMING.format(subject=plan.subject or "宣传主体"),
        style_block=plan.style_cn or "(未定画风,按题材自行设定视觉质感)",
        extra_blocks=extra,
        characters_block="本片为解说驱动的空镜宣传片,没有固定人物;主体一致性写画面/色调/场景风格的全片统一约定。",
        shots_block="\n".join(rows),
        sound_rule=SOUND_RULE_PROMO,
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise PromoFilmPromptError("模型返回了空内容,请重试一次。")
    plan.film_prompt = text
    db.commit()
    return {"chars": len(text)}
