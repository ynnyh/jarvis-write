# app/engines/promo/film_prompt.py
# -*- coding: utf-8 -*-
"""宣传片整片提示词(分段版):分镜 + 解说词 + 地标卡 → 按段切好的成片提示词文档。

外部视频模型单次最多生成 15s(少数 30s),而宣传片 60-90s——所以产出的不是一条
整片提示词,而是按镜头边界贪心切好的 N 段各自独立可用的提示词文档:每段单独贴
进模型都能生成风格一致的片段(段首逐字复述画风锚),全部生成后按段号拼接。
段间默认硬切(段边界必落镜头边界);连续运镜跨段的罕见情形标注「末帧接首帧」。

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
)

# 单段时长上限:外部模型单次生成的现实上限(15s 主流,少数支持 30s)
VALID_SEGMENT_S = (15, 30)


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


def _doc_header(seg_count: int, segment_s: int) -> str:
    """文档头(确定性,引擎写):使用说明——人看的,不属于任何单段提示词。"""
    return (
        f"【使用说明】本片共 {seg_count} 段,每段 ≤{segment_s} 秒。逐段单独生成:"
        f"把每段提示词分别贴进视频模型(单条上限 {segment_s} 秒的模型直接用;"
        f"支持更长单段的模型可自行合并相邻两段),生成完按段号顺序拼接"
        f"(剪映/ffmpeg concat),拼接处为硬切。跨段风格一致靠每段开头的画风锚;"
        f"标注「延续」的边界,用上一段末帧当本段首帧(图生视频)更稳。\n"
        f"================\n"
    )


async def build_promo_film_prompt(
    db: Session, plan: PromoPlan, progress=lambda s: None, segment_s: int = 15
) -> dict:
    """按段组装生成整片提示词文档,整体覆盖 plan.film_prompt。返回字数供 job 结果展示。"""
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

    progress(f"AI 正在把 {len(groups)} 段分镜组装成分段提示词…")
    adapter = get_adapter_for(Task.PROMO_PROMPT, timeout=300)
    prompt = SEGMENTED_FILM_PROMPT_TEMPLATE.format(
        subject=plan.subject or "宣传主体",
        total_s=total_s or int(plan.duration_s or 60),
        seg_count=len(groups),
        segment_s=segment_s,
        framing=PROMO_FRAMING.format(subject=plan.subject or "宣传主体"),
        style_block=plan.style_cn or "(未定画风,按题材自行设定视觉质感)",
        landmarks_block=_landmarks_block(plan),
        material_block=(plan.material_notes or "").strip() or "(无)",
        segments_block=_segments_block(groups, lines),
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise PromoFilmPromptError("模型返回了空内容,请重试一次。")
    plan.film_prompt = _doc_header(len(groups), segment_s) + text
    db.commit()
    return {"chars": len(plan.film_prompt), "segments": len(groups)}
