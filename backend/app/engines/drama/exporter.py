# app/engines/drama/exporter.py
# -*- coding: utf-8 -*-
"""拍摄手册导出:Markdown(人读)/ CSV(表格导入)/ JSON(程序消费)。

纯格式化,不碰 LLM/DB(数据由 API 层查好传入),方便单测。
"""
from __future__ import annotations

import csv
import io
import json

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    Project,
)
from app.engines.drama.common import MODE_DESC

_STATUS_CN = {
    "planned": "已规划",
    "scripted": "已有剧本",
    "storyboarded": "已有分镜",
    "ready": "提示词就绪",
}


def _shot_scenes(shots: list[DramaShot]) -> set[str]:
    return {s.scene_name for s in shots if s.scene_name}


def _shot_characters(shots: list[DramaShot]) -> set[str]:
    names: set[str] = set()
    for s in shots:
        names.update(s.characters or [])
    return names


def export_markdown(
    project: Project,
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None,
    cards: list[DramaCharacterCard],
    scenes: list[DramaSceneCard],
) -> str:
    """整集拍摄手册(Markdown):拿去出图/剪辑照着走。"""
    used_chars = _shot_characters(shots)
    used_scenes = _shot_scenes(shots)
    L: list[str] = []
    L.append(f"# 《{project.title}》漫剧拍摄手册 · 第 {episode.ep_index} 集《{episode.title}》")
    L.append("")
    L.append(
        f"- 模式:{MODE_DESC.get(episode.mode, episode.mode)} | 目标时长:{episode.duration_target_s} 秒"
        f" | 源章节:第 {episode.source_chapter} 章 | 状态:{_STATUS_CN.get(episode.status, episode.status)}"
    )
    L.append(f"- 开场钩子:{episode.hook}")
    L.append(f"- 结尾卡点:{episode.cliffhanger}")
    L.append("")

    if style is not None:
        L.append("## 美术风格卡(全片统一)")
        L.append(f"- 风格:{style.style_name} | 画幅:{style.ratio}")
        L.append(f"- 画风锁定段(中文):{style.style_cn}")
        L.append(f"- 画风锁定段(英文):{style.style_en}")
        L.append(f"- 负面词基座:{style.negative}")
        L.append("")

    if cards:
        L.append("## 角色卡(本集出场)")
        for c in cards:
            if c.name not in used_chars:
                continue
            L.append(f"### {c.name}" + ("(已锁定)" if c.locked else ""))
            L.append(f"- 锁定外貌:{c.appearance_cn}")
            L.append(f"- 英文锚段:{c.appearance_en}")
            if c.outfit_cn:
                L.append(f"- 标志服饰:{c.outfit_cn}")
            if c.voice_desc:
                L.append(f"- 配音声线:{c.voice_desc}")
        L.append("")

    if scenes:
        L.append("## 场景卡(本集出场)")
        for sc in scenes:
            if sc.name not in used_scenes:
                continue
            L.append(f"### {sc.name}")
            L.append(f"- 定调描述:{sc.appearance_cn}")
            if sc.appearance_en:
                L.append(f"- 英文锚段:{sc.appearance_en}")
        L.append("")

    script = episode.script or {}
    lines = script.get("lines") or []
    if lines:
        L.append("## 剧本")
        for i, l in enumerate(lines, start=1):
            if isinstance(l, dict):
                L.append(f"{i}. **{l.get('speaker', '')}**:{l.get('text', '')}")
        L.append("")

    if shots:
        L.append("## 分镜表")
        L.append("| # | 场景 | 角色 | 景别 | 运镜 | 时长(s) | 画面 | 台词 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for s in shots:
            who = "、".join(s.characters or [])
            dia = (s.dialogue or "").replace("|", "/").replace("\n", " ")
            act = (s.action_desc or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {s.seq} | {s.scene_name} | {who} | {s.shot_type} | {s.camera} "
                f"| {s.duration_s} | {act} | {dia} |"
            )
        L.append("")
        L.append("## 分镜提示词(即拿即用)")
        for s in shots:
            L.append(f"### 镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)")
            L.append(f"**中文提示词(即梦等)**")
            L.append("")
            L.append(s.prompt_cn or "(未生成)")
            L.append("")
            L.append(f"**英文提示词(Midjourney)**")
            L.append("")
            L.append(s.prompt_en or "(未生成)")
            L.append("")
            L.append(f"**负面提示词**:{s.negative or '(无)'}")
            L.append("")

    return "\n".join(L)


def export_csv(episode: DramaEpisode, shots: list[DramaShot]) -> str:
    """分镜表 CSV(带 BOM,Excel 打开中文不乱码)。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["seq", "scene_name", "characters", "shot_type", "camera", "duration_s",
         "action_desc", "dialogue", "prompt_cn", "prompt_en", "negative"]
    )
    for s in shots:
        writer.writerow(
            [s.seq, s.scene_name, "、".join(s.characters or []), s.shot_type,
             s.camera, s.duration_s, s.action_desc, s.dialogue,
             s.prompt_cn, s.prompt_en, s.negative]
        )
    return "\ufeff" + buf.getvalue()


def export_json(
    project: Project,
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None,
    cards: list[DramaCharacterCard],
    scenes: list[DramaSceneCard],
) -> str:
    """全量 JSON(结构同 API 返回,程序化处理用)。"""
    from app.engines.drama.common import (
        character_card_dict,
        episode_dict,
        scene_card_dict,
        shot_dict,
        style_card_dict,
    )

    payload = {
        "project_title": project.title,
        "episode": episode_dict(episode),
        "style": style_card_dict(style),
        "characters": [character_card_dict(c) for c in cards],
        "scenes": [scene_card_dict(sc) for sc in scenes],
        "shots": [shot_dict(s) for s in shots],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# =============== 阶段 2:字幕 / 成片包 ===============

def _srt_ts(sec: float) -> str:
    """秒 → SRT 时间码 HH:MM:SS,mmm。"""
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export_srt(shots: list[DramaShot]) -> str:
    """标准 SRT 字幕(剪映/PR 直接导入):时间轴按分镜时长累计,有台词才有字幕条。

    纯确定性输出——时间轴来自分镜表,和剪辑清单同一口径。
    """
    blocks: list[str] = []
    t = 0.0
    idx = 0
    for s in shots:
        start, end = t, t + s.duration_s
        t = end
        text = (s.dialogue or "").strip()
        if not text:
            continue
        idx += 1
        blocks.append(f"{idx}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    return "\n".join(blocks)


def export_pack_markdown(
    project: Project, episode: DramaEpisode, pack: dict
) -> str:
    """成片包 Markdown:配音稿 + 剪辑清单(TTS + 剪映照着走完出片)。"""
    L: list[str] = []
    L.append(f"# 《{project.title}》漫剧成片包 · 第 {episode.ep_index} 集《{episode.title}》")
    totals = pack.get("totals") or {}
    L.append("")
    L.append(
        f"- 模式:{MODE_DESC.get(episode.mode, episode.mode)} | 镜头:{totals.get('shots', '?')} 格"
        f" | 分镜总时长:{totals.get('storyboard_s', '?')}s(目标 {totals.get('target_s', '?')}s)"
        f" | 配音总估时:{totals.get('voice_s', '?')}s"
    )
    if pack.get("synopsis"):
        L.append(f"- 本集梗概:{pack['synopsis']}")
    L.append("")

    dubbing = pack.get("dubbing") or []
    if dubbing:
        L.append("## 配音稿(按镜头顺序)")
        L.append("")
        L.append("| # | 说话人 | 声线 | 朗读文本 | 估时/画面 | 选型建议 |")
        L.append("|---|---|---|---|---|---|")
        for d in dubbing:
            voice = str(d.get("voice") or "").replace("|", "/")
            tts = str(d.get("tts_text") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {d.get('seq')} | {d.get('speaker')} | {voice} | {tts} "
                f"| {d.get('est_s')}s/{d.get('shot_duration_s')}s | {str(d.get('tts_hint') or '').replace('|', '/')} |"
            )
        L.append("")
        for d in dubbing:
            if d.get("reading_notes"):
                L.append(f"- **{d.get('speaker')}** 朗读指示:{d['reading_notes']}")
        L.append("")

    narration = pack.get("narration_full")
    if narration:
        L.append("## 整段口播(旁白一把梭版,粘给 TTS)")
        L.append("")
        L.append(narration)
        L.append("")

    checklist = pack.get("checklist") or []
    if checklist:
        L.append("## 剪辑清单(按镜头顺序)")
        L.append("")
        L.append("| # | 场景 | 时长 | 字幕 | 转场 | 配乐 | 备注 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in checklist:
            sub = str(c.get("subtitle") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {c.get('seq')} | {c.get('scene') or ''} | {c.get('duration_s')}s "
                f"| {sub} | {c.get('transition') or ''} | {c.get('bgm_tag') or ''} | {c.get('note') or ''} |"
            )
        L.append("")
        L.append("> 出片顺序:分镜提示词出图(即梦/可灵) → 图生视频/加轻动 → 按配音稿合成语音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM。")
    return "\n".join(L)
