# app/engines/clips/exporter.py
# -*- coding: utf-8 -*-
"""情绪短片导出:手卡 Markdown / SRT 字幕 / JSON。时间轴走 media 的 SRT 内核。

手卡的装配骨架(标题/表格/台词/三轨提示词/切段)由 `media.markdown` 统一提供,
这里只写「这份手卡有哪些块、每块写什么」。
"""
from __future__ import annotations

import json

from app.db.models import MoodClip
from app.engines.clips.common import clip_dict, theme_display
from app.engines.media.audio import audio_track_note
from app.engines.media.markdown import Md
from app.engines.media.subtitles import srt_from_rows


def export_srt(row: MoodClip) -> str:
    clip = row.clip or {}
    return srt_from_rows(clip.get("shots") or [])


def export_markdown(row: MoodClip) -> str:
    clip = row.clip or {}
    shots = clip.get("shots") or []
    chunks = clip.get("chunks") or []
    name = f"{row.custom_theme or theme_display(row)} · {clip.get('take', '')}"

    md = Md()
    md.h1(f"情绪短片手卡 · {name}")
    md.bullet(
        f"主题:{theme_display(row)} | 时长:{row.duration_s}s | 画风:{row.style_name or row.direction}"
        f" | 分镜 {len(shots)} 格 · {sum(int(s.get('duration_s') or 0) for s in shots)}s"
    )
    if clip.get("logline"):
        md.bullet(f"本子:{clip['logline']}")
    if clip.get("emotion_curve"):
        md.bullet(f"情绪曲线:{clip['emotion_curve']}")
    if clip.get("hook_text"):
        md.bullet(f"投流钩子:{clip['hook_text']}")
    if clip.get("punchline"):
        md.bullet(f"**金句字幕卡:{clip['punchline']}**")
    if clip.get("quote_source"):
        md.bullet(f"金句原句(正文):{clip['quote_source']}")
    md.add("")

    if clip.get("cautions"):
        md.quote(f"⚠ 需核实:{';'.join(clip['cautions'])}", blank=True)
    if clip.get("style_cn"):
        md.style_anchor(row.style_cn, row.style_en, row.negative)

    lines = clip.get("lines") or []
    if lines:
        md.h2("台词")
        md.speech(lines)

    cards = clip.get("character_cards") or []
    if cards:
        md.h2("角色定妆卡(参考图用)")
        md.quote("复制每张卡的描述去文生图出定妆图,再上传作参考图,人物才不会漂。",
                 blank=True)
        for c in cards:
            md.bullet(f"**{c.get('name', '')}**:{c.get('desc', '')}")
        md.add("")

    if shots:
        md.h2("分镜")
        md.table(
            ["#", "场景", "景别", "运镜", "秒", "画面", "台词"],
            [
                [s.get("seq"), s.get("scene_name"), s.get("shot_type"), s.get("camera"),
                 f"{s.get('duration_s')}s", s.get("action_desc"), s.get("dialogue")]
                for s in shots
            ],
        )
        md.h2("三轨提示词")
        for s in shots:
            md.shot_tracks(s.get("seq"), s.get("shot_type"), s.get("camera"),
                           s.get("duration_s"), s.get("prompt_cn"), s.get("prompt_en"),
                           s.get("negative"))
        if chunks:
            md.segments(chunks)
        md.quote("出片:按段生成 → 画布拼接 → 压 SRT → 末格加金句字幕卡。", blank=True)
        # 音频口径:15s 短片常常一段就出完,这时模型自带音频直接可用(口径见 media.audio)
        md.extend(audio_track_note(single_segment=len(chunks) <= 1))
    return md.text()


def export_json(row: MoodClip) -> str:
    return json.dumps(clip_dict(row), ensure_ascii=False, indent=2)
