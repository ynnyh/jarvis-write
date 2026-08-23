# app/engines/promo/exporter.py
# -*- coding: utf-8 -*-
"""宣传片导出:拍摄手册 Markdown / 分镜 CSV / SRT 字幕(时间轴复用漫剧的确定性内核)。"""
from __future__ import annotations

import csv
import io
import json

from app.db.models import PromoPlan, PromoShot
from app.engines.drama.exporter import _srt_blocks  # 同 app 内复用确定性字幕内核
from app.engines.promo.common import STATUS_CN, angle_labels


def export_srt(shots: list[PromoShot]) -> str:
    return _srt_blocks([(s.duration_s, s.dialogue or "") for s in shots])


def export_csv(shots: list[PromoShot]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["seq", "scene_name", "shot_type", "camera", "duration_s",
         "action_desc", "dialogue", "prompt_cn", "prompt_en", "negative"]
    )
    for s in shots:
        writer.writerow(
            [s.seq, s.scene_name, s.shot_type, s.camera, s.duration_s,
             s.action_desc, s.dialogue, s.prompt_cn, s.prompt_en, s.negative]
        )
    return "\ufeff" + buf.getvalue()


def export_markdown(plan: PromoPlan, shots: list[PromoShot]) -> str:
    L: list[str] = []
    name = plan.title or plan.subject
    L.append(f"# 《{name}》宣传片拍摄手册")
    L.append("")
    L.append(
        f"- 主题:{plan.subject} | 角度:{angle_labels(plan.angles)} | 时长:{plan.duration_s}s"
        f" | 画风:{plan.style_name or plan.direction} | 状态:{STATUS_CN.get(plan.status, plan.status)}"
    )
    brief = plan.brief or {}
    if brief.get("positioning"):
        L.append(f"- 定位:{brief['positioning']}")
    if brief.get("cautions"):
        L.append(f"- ⚠ 需人工核实:{';'.join(brief['cautions'])}")
    L.append("")

    if brief.get("structure"):
        L.append("## 创作简报")
        L.append("")
        segs = brief.get("structure") or []
        L.append("| 段落 | 角度 | 秒 | 内容 |")
        L.append("|---|---|---|---|")
        for s in segs:
            if isinstance(s, dict):
                beat = str(s.get("beat") or "").replace("|", "/")
                L.append(f"| {s.get('title')} | {s.get('angle')} | {s.get('seconds')}s | {beat} |")
        if brief.get("slogan_candidates"):
            L.append("")
            L.append(f"**Slogan 候选**:{' / '.join(brief['slogan_candidates'])}")
        L.append("")

    if plan.style_cn:
        L.append("## 视觉风格卡")
        L.append("")
        L.append(f"- 风格:{plan.style_name}")
        L.append(f"- 画风锁定段(中文):{plan.style_cn}")
        L.append(f"- 画风锁定段(英文):{plan.style_en}")
        L.append(f"- 负面词基座:{plan.negative}")
        L.append("")

    landmarks = [l for l in (plan.landmarks or []) if isinstance(l, dict) and l.get("name")]
    if landmarks:
        L.append("## 地标卡")
        L.append("")
        for l in landmarks:
            L.append(f"### {l.get('name')}")
            L.append(f"- 定调:{l.get('appearance_cn')}")
            L.append(f"- 英文锚:{l.get('appearance_en')}")
        L.append("")

    script = plan.script or {}
    lines = script.get("lines") or []
    if lines:
        L.append("## 解说词")
        L.append("")
        for i, l in enumerate(lines, start=1):
            if isinstance(l, dict):
                L.append(f"{i}. {l.get('text', '')}")
                if l.get("action"):
                    L.append(f"   <sub>画面:{l['action']}</sub>")
        L.append("")

    if shots:
        L.append("## 分镜表")
        L.append("")
        L.append("| # | 场景 | 景别 | 运镜 | 秒 | 画面 | 解说词 |")
        L.append("|---|---|---|---|---|---|---|")
        for s in shots:
            dia = (s.dialogue or "").replace("|", "/").replace("\n", " ")
            act = (s.action_desc or "").replace("|", "/").replace("\n", " ")
            L.append(f"| {s.seq} | {s.scene_name} | {s.shot_type} | {s.camera} | {s.duration_s}s | {act} | {dia} |")
        L.append("")
        prompted = [s for s in shots if s.prompt_cn or s.prompt_en]
        if prompted:
            L.append("## 三轨提示词(即拿即用)")
            L.append("")
            for s in prompted:
                L.append(f"### 镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)")
                L.append("**中文提示词(即梦/可灵)**")
                L.append("")
                L.append(s.prompt_cn or "(未生成)")
                L.append("")
                L.append("**英文提示词(Midjourney)**")
                L.append("")
                L.append(s.prompt_en or "(未生成)")
                L.append("")
                L.append(f"**负面提示词**:{s.negative or '(无)'}")
                L.append("")

    chunks = plan.chunks or {}
    if chunks.get("items"):
        L.append(f"## 生成切段(每段 ≤{chunks.get('chunk_s', '?')}s,一段一次生成,画布拼接)")
        L.append("")
        L.append("| 段 | 时间码 | 秒 | 镜头 | 场景 | 超限 |")
        L.append("|---|---|---|---|---|---|")
        for c in chunks["items"]:
            seqs = "、".join(str(q) for q in c.get("shot_seqs") or [])
            scenes = "、".join(c.get("scenes") or [])
            over = "⚠ 超" if c.get("over_limit") else ""
            L.append(
                f"| {c.get('index')} | {c.get('start_s')}-{c.get('end_s')}s "
                f"| {c.get('duration_s')}s | {seqs} | {scenes} | {over} |"
            )
        L.append("")
        for c in chunks["items"]:
            L.append(f"### 段 {c.get('index')}({c.get('start_s')}-{c.get('end_s')}s)")
            L.append(f"**视频提示词(文生视频)**")
            L.append("")
            L.append(c.get("motion_prompt_cn") or "(未生成)")
            L.append("")
            L.append(f"**英文视频提示词**")
            L.append("")
            L.append(c.get("motion_prompt_en") or "(未生成)")
            L.append("")
            L.append(f"**首帧指引**:{c.get('first_frame_hint') or '(用本段首格静帧)'}")
            L.append("")
            L.append(f"**拼接提示**:{c.get('link_note') or '(硬切)'}")
            L.append("")

    pack = plan.pack or {}
    if pack.get("checklist"):
        totals = pack.get("totals") or {}
        L.append("## 成片包")
        L.append("")
        L.append(
            f"镜头 {totals.get('shots', '?')} 格 | 分镜总时长 {totals.get('storyboard_s', '?')}s"
            f"(目标 {totals.get('target_s', '?')}s) | 解说估时 {totals.get('voice_s', '?')}s"
        )
        L.append("")
        L.append("| # | 场景 | 秒 | 字幕 | 转场 | 配乐 | 备注 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in pack["checklist"]:
            sub = str(c.get("subtitle") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {c.get('seq')} | {c.get('scene') or ''} | {c.get('duration_s')}s "
                f"| {sub} | {c.get('transition') or ''} | {c.get('bgm_tag') or ''} | {c.get('note') or ''} |"
            )
        L.append("")
        if pack.get("narration_full"):
            L.append("## 整段解说(粘给 TTS 一把梭)")
            L.append("")
            L.append(str(pack["narration_full"]))
            L.append("")
        L.append("> 出片顺序:分镜提示词出图 → 图生视频/加轻动 → 解说词配音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM → 收束加 slogan 字幕。")
    return "\n".join(L)


def export_json(plan: PromoPlan, shots: list[PromoShot]) -> str:
    from app.engines.promo.common import plan_dict, shot_dict

    payload = {
        "plan": {k: v for k, v in plan_dict(plan).items()},
        "shots": [shot_dict(s) for s in shots],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
