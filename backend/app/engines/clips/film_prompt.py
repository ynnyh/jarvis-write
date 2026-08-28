# app/engines/clips/film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(情绪短片/灵感工坊/故事工坊):选中本子 + 风格卡 → 一条出一整片。

原料来自 mood_clips 行内:clip.shots(分镜,字段名与 drama_shots 同)、clip.lines
(台词,供说话人反查)、风格卡三列、theme/inspiration(命题或点子)。与逐段
出片链(clip.chunks 喂出片引擎)互补:这条是整段贴进端到端模型的成品稿。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.engines.media.text import clip as _clip, speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import (
    CLIP_FRAMINGS,
    FILM_PROMPT_TEMPLATE,
    SOUND_RULE_SILENT,
    SOUND_RULE_VOICED,
)

_MODE_LABELS = {"mood": "情绪短片", "play": "灵感玩法短片", "free": "故事短片"}
_DIALOGUE_STYLE_RULE = {
    "voiceover": "本片台词以旁白/独白形态出现,与画面逐音节同步",
    "silent": "本片无台词",
}


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
    return f"【金句/收束(镜头表的落点要接住它)】{punch}\n" if punch else ""


async def build_clip_film_prompt(db: Session, row, progress=lambda s: None) -> dict:
    """组装生成整片提示词,整体覆盖 row.film_prompt。返回字数供 job 结果展示。"""
    clip = row.clip or {}
    shots = [s for s in (clip.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        raise ClipFilmPromptError(
            "这条短片还没有可用分镜:先在工坊里选定一个本子,再来生成整片提示词。"
        )
    lines = clip.get("lines") or []

    mode = row.mode if row.mode in CLIP_FRAMINGS else "mood"
    total_s = sum(int(s.get("duration_s") or 0) for s in shots) or int(row.duration_s or 15)
    silent = str(row.dialogue_style or "") == "silent"

    progress("AI 正在把分镜组装成整片提示词…")
    adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
    prompt = FILM_PROMPT_TEMPLATE.format(
        workshop_label=_MODE_LABELS[mode],
        title_line=_theme_line(row),
        ratio_total=f"9:16 竖屏,{total_s} 秒左右",
        framing=CLIP_FRAMINGS[mode],
        style_block=row.style_cn or "(未定画风,按题材自行设定视觉质感)",
        extra_blocks=_punchline_block(clip),
        characters_block=(
            "本片没有人物卡:若有固定出镜人物,外貌/服饰自行定死并全片一致;空镜则写画面主体。"
        ),
        shots_block=_shots_block(shots, lines),
        sound_rule=SOUND_RULE_SILENT if silent else SOUND_RULE_VOICED,
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise ClipFilmPromptError("模型返回了空内容,请重试一次。")
    row.film_prompt = text
    db.commit()
    return {"chars": len(text)}


def _shots_block(shots: list[dict], lines: list) -> str:
    """分镜清单:时长累计交给模型;台词带说话人(按 lines 文本反查,对不上不猜)。"""
    rows = []
    for s in shots:
        try:
            seq = int(s.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        dialogue = str(s.get("dialogue") or "").strip()
        speaker = speaker_of(dialogue, lines)
        row = (
            f"- 镜头{seq}|{str(s.get('shot_type') or '中景').strip()}"
            f"|运镜:{str(s.get('camera') or '固定').strip()}"
            f"|{int(s.get('duration_s') or 0)}秒"
            f"|画面:{_clip(str(s.get('action_desc') or '').strip(), 80) or '未写'}"
        )
        if dialogue:
            row += f"|台词:{f'{speaker}:' if speaker else ''}{dialogue}"
        rows.append(row)
    return "\n".join(rows)
