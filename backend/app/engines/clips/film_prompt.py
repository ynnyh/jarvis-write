# app/engines/clips/film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(情绪短片/灵感工坊/故事工坊,单条版):选中本子 + 风格卡 → 一条合并的整片提示词。

原料来自 mood_clips 行内:clip.shots(分镜,字段名与 drama_shots 同)、clip.lines
(台词,供说话人反查)、风格卡三列、theme/inspiration(命题或点子)。15/30 秒短片
装得进外部端到端模型单次生成上限,所以产物是**一条**把分镜合并成连续场景的完整
提示词,整段复制贴出去一次出片——不再切分段文档;逐段出片仍走 clip.chunks 喂出片
引擎那条链,两不耽误。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.engines.media.text import clip as _clip, speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import CLIP_FRAMINGS, WHOLE_CLIP_PROMPT_TEMPLATE

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
    return "\n".join(f"- {n}(没有角色卡:外貌/服饰自行定死,全片完全一致)" for n in names)


def _storyboard_block(shots: list[dict], lines: list) -> str:
    """分镜原料:一行一镜,时间码按时长累计,台词带说话人(按 lines 文本反查)。"""
    rows = []
    t = 0
    for s in shots:
        try:
            seq = int(s.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        dur = int(s.get("duration_s") or 0)
        row = (
            f"- {t}—{t + dur}秒|镜头{seq}|{str(s.get('shot_type') or '中景').strip()}"
            f"|运镜:{str(s.get('camera') or '固定').strip()}"
            f"|画面:{_clip(str(s.get('action_desc') or '').strip(), 80) or '未写'}"
        )
        t += dur
        d = str(s.get("dialogue") or "").strip()
        if d:
            sp = speaker_of(d, lines)
            row += f"|台词{f'({sp})' if sp else ''}:{d}"
        rows.append(row)
    return "\n".join(rows)


async def build_clip_film_prompt(db: Session, row, progress=lambda s: None) -> dict:
    """把分镜合并生成一条整片提示词,整体覆盖 row.film_prompt。返回字数与全片时长。"""
    clip = row.clip or {}
    shots = [s for s in (clip.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        raise ClipFilmPromptError(
            "这条短片还没有可用分镜:先在工坊里选定一个本子,再来生成整片提示词。"
        )
    lines = clip.get("lines") or []

    mode = row.mode if row.mode in CLIP_FRAMINGS else "mood"
    total_s = sum(int(s.get("duration_s") or 0) for s in shots) or int(row.duration_s or 15)
    # 篇幅只设下限(用户明确要求不压缩):细节量决定下限高度,上不封顶
    length_guide = "600 字" if total_s <= 15 else "1000 字"

    progress("AI 正在把分镜合并成一条整片提示词…")
    adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
    prompt = WHOLE_CLIP_PROMPT_TEMPLATE.format(
        workshop_label=_MODE_LABELS[mode],
        title_line=_theme_line(row),
        total_s=total_s,
        ratio="9:16 竖屏",
        framing=CLIP_FRAMINGS[mode],
        style_block=row.style_cn or "(未定画风,按题材自行设定视觉质感)",
        extra_blocks=_punchline_block(clip),
        characters_block=_characters_block(clip),
        storyboard_block=_storyboard_block(shots, lines),
        length_guide=length_guide,
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise ClipFilmPromptError("模型返回了空内容,请重试一次。")
    row.film_prompt = text
    db.commit()
    return {"chars": len(row.film_prompt), "duration_s": total_s}
