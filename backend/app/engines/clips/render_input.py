# app/engines/clips/render_input.py
# -*- coding: utf-8 -*-
"""情绪短片:段(chunk)→ 出片引擎提交参数(线内构造,供 api/render.py 调用)。

情绪线没有运动轨(那是漫剧 drama_shots 的 motion_cn/en),出片提示词只能确定性
拼:段内每格一行「镜头{运镜};{画面动作}」,末尾统一加保守运动兜底——宁朴素
不可空,这句是图生视频唯一的动作输入。出片单位是**段**(clip.chunks,一次生成
一段),首帧用该段挂的第一张参考图(由 api 层从 ClipShoot 取,这里不管)。
"""
from __future__ import annotations

from app.engines.media.text import clip as _clip
from app.engines.media.video import (
    clamp_duration_s,
    resolution_value,
    video_negative,
)


def _shot_rows(mood_clip) -> dict[int, dict]:
    """clip.shots 按 seq 建索引(shot JSON 字段名与 drama_shots 列同名)。"""
    rows: dict[int, dict] = {}
    for s in (mood_clip.clip or {}).get("shots") or []:
        if isinstance(s, dict) and s.get("seq") is not None:
            try:
                rows[int(s["seq"])] = s
            except (TypeError, ValueError):
                continue
    return rows


def chunk_render_payload(mood_clip, chunk: dict, quality: str = "768p") -> dict:
    """一个切段 → 出片引擎的提交参数。

    chunk 是 clip.chunks 里的一项(index/start_s/end_s/shot_seqs/...);
    情绪线无画风卡 ratio,画幅固定竖屏(短视频默认),画质档来自出片配置。
    """
    shots = _shot_rows(mood_clip)
    seqs = [int(q) for q in (chunk.get("shot_seqs") or [])]

    lines: list[str] = []
    for q in seqs:
        s = shots.get(q) or {}
        cam = (s.get("camera") or "").strip() or "固定"
        act = (
            (s.get("action_desc") or "").strip()
            or (s.get("prompt_cn") or "").strip()
            or "保持姿态,只有呼吸与衣料的细微浮动"
        )
        lines.append(f"镜头{cam};{_clip(act, 80)}")
    motion = ";然后".join(lines) if lines else "保持姿态,只有呼吸与衣料的细微浮动"

    try:
        span = int(chunk.get("end_s", 0) or 0) - int(chunk.get("start_s", 0) or 0)
    except (TypeError, ValueError):
        span = 0
    duration = clamp_duration_s(span, upper=15, default=5)

    prompt = "\n".join(
        [
            f"【怎么动】{motion};整体幅度小、速度平稳,人物长相与服饰保持不变",
            f"【时长】{duration} 秒",
            f"【不要出现】{video_negative((mood_clip.negative or '').strip())}",
        ]
    )
    return {
        "prompt": prompt,
        "duration_s": duration,
        "resolution": resolution_value(quality, "9:16"),
    }
