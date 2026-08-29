# app/engines/promo/film_prompt.py
# -*- coding: utf-8 -*-
"""宣传片整片提示词(分段版):分镜 + 解说词 + 地标卡 → 按段切好的成片提示词文档。

外部视频模型单次最多生成 15s(少数 30s),而宣传片 60-90s——所以产出的不是一条
整片提示词,而是按镜头边界贪心切好的 N 段各自独立可用的提示词文档:每段单独贴
进模型都能生成风格一致的片段(段首逐字复述画风锚),全部生成后按段号拼接。
素材点(material_notes)是解说与画面的硬约束;宣传片以空镜为主,无固定人物。

与内部出片链的切段(chunks,喂自家出片引擎)互补:这份是给外部端到端模型的成品稿。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.media.segments import group_by_limit
from app.engines.media.text import speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import (
    PROMO_FRAMING,
    SEGMENTED_FILM_PROMPT_TEMPLATE,
    VALID_SEGMENT_S,
    segmented_doc_header,
)


class PromoFilmPromptError(ValueError):
    """整片提示词生成的业务性错误(信息直接上屏)。"""


def _landmarks_block(plan: PromoPlan) -> str:
    rows = [
        f"- {str(l.get('name') or '').strip()}:{str(l.get('appearance_cn') or '').strip()}"
        for l in (plan.landmarks or [])
        if isinstance(l, dict) and str(l.get("name") or "").strip()
    ]
    return "\n".join(rows) if rows else "(未生成地标卡)"


def _segments_block(groups: list[list[PromoShot]], lines: list) -> str:
    """分段计划原料:每段的镜头行 + 该段覆盖的解说词(带说话人)。"""
    rows = []
    t = 0
    for i, group in enumerate(groups, 1):
        start, end = t, t + sum(int(s.duration_s or 0) for s in group)
        t = end
        seg_rows = [
            f"  - 镜头{s.seq}|{s.shot_type or '空镜'}|运镜:{s.camera or '固定'}|"
            f"{s.duration_s}秒|场景:{s.scene_name or '未标'}|画面:{(s.action_desc or '').strip() or '未写'}"
            for s in group
        ]
        block = f"【第{i}段|{start}—{end}秒】\n" + "\n".join(seg_rows)
        for s in group:
            d = (s.dialogue or "").strip()
            if not d:
                continue
            sp = speaker_of(d, lines)
            block += f"\n  解说词{f'({sp})' if sp else ''}:{d}"
        rows.append(block)
    return "\n".join(rows)


async def build_promo_film_prompt(
    db: Session, plan: PromoPlan, progress=lambda s: None, segment_s: int = 15
) -> dict:
    """按段组装生成整片提示词文档,整体覆盖 plan.film_prompt。返回字数与段数。"""
    if segment_s not in VALID_SEGMENT_S:
        raise PromoFilmPromptError("单段时长只支持 15 / 30 秒。")

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

    groups = group_by_limit(shots, segment_s)
    total_s = sum(int(s.duration_s or 0) for s in shots)

    extra = f"【地标卡(涉及镜头的场景描述贴着写)】\n{_landmarks_block(plan)}\n"
    if (plan.material_notes or "").strip():
        extra += f"【素材点(卖点/事实,解说与画面的硬约束)】{plan.material_notes.strip()}\n"

    progress(f"AI 正在把 {len(groups)} 段分镜组装成分段提示词…")
    adapter = get_adapter_for(Task.PROMO_PROMPT, timeout=300)
    prompt = SEGMENTED_FILM_PROMPT_TEMPLATE.format(
        workshop_label="宣传片企划",
        title_line=f"{plan.subject or '未填主体'} · {plan.title or '未命名'}",
        total_s=total_s or int(plan.duration_s or 60),
        seg_count=len(groups),
        segment_s=segment_s,
        ratio="9:16 竖屏",
        framing=PROMO_FRAMING.format(subject=plan.subject or "宣传主体"),
        style_block=plan.style_cn or "(未定画风,按题材自行设定视觉质感)",
        extra_blocks=extra,
        characters_block="本片为解说驱动的空镜宣传片,没有固定人物;主体一致性写画面/色调/场景风格的全片统一约定。",
        segments_block=_segments_block(groups, lines),
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise PromoFilmPromptError("模型返回了空内容,请重试一次。")
    plan.film_prompt = segmented_doc_header(len(groups), segment_s) + text
    db.commit()
    return {"chars": len(plan.film_prompt), "segments": len(groups)}
