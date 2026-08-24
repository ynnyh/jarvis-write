# app/engines/clips/exporter.py
# -*- coding: utf-8 -*-
"""情绪短片导出:手卡 Markdown / SRT 字幕 / JSON。时间轴复用漫剧 SRT 内核。"""
from __future__ import annotations

import json

from app.db.models import MoodClip
from app.engines.drama.exporter import _srt_blocks
from app.engines.media.audio import audio_track_note
from app.engines.clips.common import clip_dict, theme_display


def _srt_from_shots(shots: list[dict]) -> str:
    return _srt_blocks([(int(s.get("duration_s") or 0), str(s.get("dialogue") or "")) for s in shots])


def export_srt(row: MoodClip) -> str:
    clip = row.clip or {}
    return _srt_from_shots(clip.get("shots") or [])


def export_markdown(row: MoodClip) -> str:
    clip = row.clip or {}
    shots = clip.get("shots") or []
    name = f"{row.custom_theme or theme_display(row)} · {clip.get('take', '')}"
    L: list[str] = []
    L.append(f"# 情绪短片手卡 · {name}")
    L.append("")
    L.append(
        f"- 主题:{theme_display(row)} | 时长:{row.duration_s}s | 画风:{row.style_name or row.direction}"
        f" | 分镜 {len(shots)} 格 · {sum(int(s.get('duration_s') or 0) for s in shots)}s"
    )
    if clip.get("logline"):
        L.append(f"- 本子:{clip['logline']}")
    if clip.get("emotion_curve"):
        L.append(f"- 情绪曲线:{clip['emotion_curve']}")
    if clip.get("hook_text"):
        L.append(f"- 投流钩子:{clip['hook_text']}")
    if clip.get("punchline"):
        L.append(f"- **金句字幕卡:{clip['punchline']}**")
    if clip.get("quote_source"):
        L.append(f"- 金句原句(正文):{clip['quote_source']}")
    L.append("")
    if clip.get("cautions"):
        L.append(f"> ⚠ 需核实:{';'.join(clip['cautions'])}")
        L.append("")
    if clip.get("style_cn"):
        L.append("## 画风锚")
        L.append("")
        L.append(f"- {row.style_cn}")
        L.append(f"- EN: {row.style_en}")
        L.append(f"- 负面基座:{row.negative}")
        L.append("")
    lines = clip.get("lines") or []
    if lines:
        L.append("## 台词")
        L.append("")
        for l in lines:
            L.append(f"- **{l.get('speaker', '')}**:{l.get('text', '')}")
        L.append("")
    if shots:
        L.append("## 分镜")
        L.append("")
        L.append("| # | 场景 | 景别 | 运镜 | 秒 | 画面 | 台词 |")
        L.append("|---|---|---|---|---|---|---|")
        for s in shots:
            dia = str(s.get("dialogue") or "").replace("|", "/")
            act = str(s.get("action_desc") or "").replace("|", "/")
            L.append(
                f"| {s.get('seq')} | {s.get('scene_name') or ''} | {s.get('shot_type')} "
                f"| {s.get('camera')} | {s.get('duration_s')}s | {act} | {dia} |"
            )
        L.append("")
        L.append("## 三轨提示词")
        L.append("")
        for s in shots:
            L.append(f"### 镜头 {s.get('seq')}({s.get('shot_type')}/{s.get('camera')}/{s.get('duration_s')}s)")
            L.append(f"**中文(即梦/可灵)**")
            L.append("")
            L.append(s.get("prompt_cn") or "(未生成)")
            L.append("")
            L.append(f"**英文(MJ)**")
            L.append("")
            L.append(s.get("prompt_en") or "(未生成)")
            L.append("")
            L.append(f"**负面**:{s.get('negative') or '(无)'}")
            L.append("")
        chunks = clip.get("chunks") or []
        if chunks:
            L.append("## 生成切段(一段一次生成,画布拼接)")
            L.append("")
            for c in chunks:
                over = " ⚠超限" if c.get("over_limit") else ""
                L.append(
                    f"- **段 {c.get('index')}**({c.get('start_s')}-{c.get('end_s')}s,"
                    f"镜头 {('、'.join(str(q) for q in c.get('shot_seqs') or []))}){over}"
                )
            L.append("")
        L.append("> 出片:按段生成 → 画布拼接 → 压 SRT → 末格加金句字幕卡。")
        L.append("")
        # 音频口径:15s 短片常常一段就出完,这时模型自带音频直接可用(口径见 media.audio)
        L.extend(audio_track_note(single_segment=len(chunks) <= 1))
    return "\n".join(L)


def export_json(row: MoodClip) -> str:
    return json.dumps(clip_dict(row), ensure_ascii=False, indent=2)
