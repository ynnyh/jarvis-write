# app/engines/clips/film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(情绪短片/灵感工坊/故事工坊,分段版):选中本子 + 风格卡 → 按段切好的文档。

原料来自 mood_clips 行内:clip.shots(分镜,字段名与 drama_shots 同)、clip.lines
(台词,供说话人反查)、风格卡三列、theme/inspiration(命题或点子)。15s 短片
一段装下;30s 短片按镜头边界切成两段。与逐段出片链(clip.chunks 喂出片引擎)
互补:这份是给外部端到端模型的成品稿。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.engines.media.segments import group_by_limit
from app.engines.media.text import clip as _clip, speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import (
    CLIP_FRAMINGS,
    SEGMENTED_FILM_PROMPT_TEMPLATE,
    VALID_SEGMENT_S,
    segmented_doc_header,
)

_MODE_LABELS = {"mood": "情绪短片", "play": "灵感玩法短片", "free": "故事短片"}


class ClipFilmPromptError(ValueError):
    """整片提示词生成的业务性错误(信息直接上屏)。"""


def _theme_line(row) -> str:
    """命题/点子一行:灵感工坊与故事工坊以用户点子为主轴,必须进原料。"""
    parts = [str(row.custom_theme or "").strip()]
    insp = str(row.inspiration or "").strip()
    if insp:
        parts.append(f"点子:{insp}")
    return " / ".join(p for p in parts if p) or "未填写命题,按分镜内容自行归纳"


def _punchline_block(clip: dict) -> str:
    punch = str(clip.get("punchline") or "").strip()
    return f"【金句/收束(最后一段要接住它)】{punch}\n" if punch else ""


def _characters_block(clip: dict) -> str:
    names: list[str] = []
    for shot in clip.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        for name in shot.get("characters") or []:
            if name and name not in names:
                names.append(name)
    if not names:
        return "本片没有人物卡:若有固定出镜人物,外貌/服饰自行定死并全片一致;空镜则写画面主体。"
    return "\n".join(f"- {n}(没有角色卡:外貌/服饰自行定死,全片各段完全一致)" for n in names)


def _segments_block(groups: list[list[dict]], lines: list) -> str:
    """分段计划原料:每段的镜头行 + 该段覆盖的台词(带说话人,按 lines 文本反查)。"""
    rows = []
    t = 0
    for i, group in enumerate(groups, 1):
        start, end = t, t + sum(int(s.get("duration_s") or 0) for s in group)
        t = end
        seg_rows = []
        for s in group:
            try:
                seq = int(s.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            seg_rows.append(
                f"  - 镜头{seq}|{str(s.get('shot_type') or '中景').strip()}"
                f"|运镜:{str(s.get('camera') or '固定').strip()}"
                f"|{int(s.get('duration_s') or 0)}秒"
                f"|画面:{_clip(str(s.get('action_desc') or '').strip(), 80) or '未写'}"
            )
        block = f"【第{i}段|{start}—{end}秒】\n" + "\n".join(seg_rows)
        for s in group:
            d = str(s.get("dialogue") or "").strip()
            if not d:
                continue
            sp = speaker_of(d, lines)
            block += f"\n  台词{f'({sp})' if sp else ''}:{d}"
        rows.append(block)
    return "\n".join(rows)


async def build_clip_film_prompt(
    db: Session, row, progress=lambda s: None, segment_s: int = 15
) -> dict:
    """按段组装生成整片提示词文档,整体覆盖 row.film_prompt。返回字数与段数。"""
    if segment_s not in VALID_SEGMENT_S:
        raise ClipFilmPromptError("单段时长只支持 15 / 30 秒。")

    clip = row.clip or {}
    shots = [s for s in (clip.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        raise ClipFilmPromptError(
            "这条短片还没有可用分镜:先在工坊里选定一个本子,再来生成整片提示词。"
        )
    lines = clip.get("lines") or []

    mode = row.mode if row.mode in CLIP_FRAMINGS else "mood"
    groups = group_by_limit(shots, segment_s)
    total_s = sum(int(s.get("duration_s") or 0) for s in shots) or int(row.duration_s or 15)

    progress(f"AI 正在把 {len(groups)} 段分镜组装成分段提示词…")
    adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
    prompt = SEGMENTED_FILM_PROMPT_TEMPLATE.format(
        workshop_label=_MODE_LABELS[mode],
        title_line=_theme_line(row),
        total_s=total_s,
        seg_count=len(groups),
        segment_s=segment_s,
        ratio="9:16 竖屏",
        framing=CLIP_FRAMINGS[mode],
        style_block=row.style_cn or "(未定画风,按题材自行设定视觉质感)",
        extra_blocks=_punchline_block(clip),
        characters_block=_characters_block(clip),
        segments_block=_segments_block(groups, lines),
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise ClipFilmPromptError("模型返回了空内容,请重试一次。")
    row.film_prompt = segmented_doc_header(len(groups), segment_s) + text
    db.commit()
    return {"chars": len(row.film_prompt), "segments": len(groups)}
