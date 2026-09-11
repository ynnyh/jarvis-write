# app/engines/promo/exporter.py
# -*- coding: utf-8 -*-
"""宣传片导出:拍摄手册 Markdown / 分镜 CSV / SRT 字幕(时间轴走 media 的确定性内核)。

手册骨架(标题/表格/三轨提示词/小结)由 `media.markdown` 统一提供;CSV 的 BOM 与
SRT 的时间轴在 `media.text` / `media.subtitles`,这里只写宣传片有哪些块。
"""
from __future__ import annotations

import json

from app.db.models import PromoPlan, PromoShot
from app.engines.media.audio import audio_track_note
from app.engines.media.markdown import Md
from app.engines.media.subtitles import srt_blocks
from app.engines.media.text import csv_text
from app.engines.promo.common import STATUS_CN, angle_labels

# 三轨提示词的宣传片文案(手册里「即拿即用」那节)
_CN_LABEL = "**中文提示词(即梦/可灵)**"
_EN_LABEL = "**英文提示词(Midjourney)**"
_NEG_LABEL = "**负面提示词**"


def export_srt(shots: list[PromoShot]) -> str:
    return srt_blocks([(s.duration_s, s.dialogue or "") for s in shots])


def export_csv(shots: list[PromoShot]) -> str:
    header = ["seq", "scene_name", "shot_type", "camera", "duration_s",
              "action_desc", "dialogue", "prompt_cn", "prompt_en", "negative"]
    rows = [
        [s.seq, s.scene_name, s.shot_type, s.camera, s.duration_s,
         s.action_desc, s.dialogue, s.prompt_cn, s.prompt_en, s.negative]
        for s in shots
    ]
    return csv_text(header, rows)


def export_markdown(plan: PromoPlan, shots: list[PromoShot]) -> str:
    brief = plan.brief or {}
    md = Md()
    md.h1(f"《{plan.title or plan.subject}》宣传片拍摄手册")
    md.kv([
        ("主题", plan.subject),
        ("角度", angle_labels(plan.angles)),
        ("时长", f"{plan.duration_s}s"),
        ("画风", plan.style_name or plan.direction),
        ("状态", STATUS_CN.get(plan.status, plan.status)),
    ], blank=False)  # 定位/需核实的条目紧跟其后,中间不留空行
    if brief.get("positioning"):
        md.bullet(f"定位:{brief['positioning']}")
    if brief.get("cautions"):
        md.bullet(f"⚠ 需人工核实:{';'.join(brief['cautions'])}")
    md.add("")

    if brief.get("structure"):
        md.h2("创作简报")
        md.table(
            ["段落", "角度", "秒", "内容"],
            [
                [s.get("title"), s.get("angle"), f"{s.get('seconds')}s", s.get("beat")]
                for s in brief["structure"] if isinstance(s, dict)
            ],
            blank=False,  # Slogan 候选那两行自己带前置空行
        )
        if brief.get("slogan_candidates"):
            md.add("")
            md.add(f"**Slogan 候选**:{' / '.join(brief['slogan_candidates'])}")
        md.add("")

    if plan.style_cn:
        md.h2("视觉风格卡")
        md.bullet(f"风格:{plan.style_name}")
        md.bullet(f"画风锁定段(中文):{plan.style_cn}")
        md.bullet(f"画风锁定段(英文):{plan.style_en}")
        md.bullet(f"负面词基座:{plan.negative}")
        md.add("")

    landmarks = [l for l in (plan.landmarks or []) if isinstance(l, dict) and l.get("name")]
    if landmarks:
        md.h2("地标卡")
        for l in landmarks:
            md.h3(l.get("name"))
            md.bullet(f"定调:{l.get('appearance_cn')}")
            md.bullet(f"英文锚:{l.get('appearance_en')}")
        md.add("")

    lines = (plan.script or {}).get("lines") or []
    if lines:
        md.h2("解说词")
        for i, l in enumerate(lines, start=1):
            if not isinstance(l, dict):
                continue
            md.add(f"{i}. {l.get('text', '')}")
            if l.get("action"):
                md.add(f"   <sub>画面:{l['action']}</sub>")
        md.add("")

    if shots:
        md.h2("分镜表")
        md.table(
            ["#", "场景", "景别", "运镜", "秒", "画面", "解说词"],
            [
                [s.seq, s.scene_name, s.shot_type, s.camera, f"{s.duration_s}s",
                 s.action_desc, s.dialogue]
                for s in shots
            ],
        )
        prompted = [s for s in shots if s.prompt_cn or s.prompt_en]
        if prompted:
            md.h2("三轨提示词(即拿即用)")
            for s in prompted:
                md.shot_tracks(s.seq, s.shot_type, s.camera, s.duration_s, s.prompt_cn,
                               s.prompt_en, s.negative,
                               cn_label=_CN_LABEL, en_label=_EN_LABEL, neg_label=_NEG_LABEL)

    chunks = (plan.chunks or {}).get("items") or []
    if chunks:
        limit = (plan.chunks or {}).get("chunk_s", "?")
        md.h2(f"生成切段(每段 ≤{limit}s,一段一次生成,画布拼接)")
        md.table(
            ["段", "时间码", "秒", "镜头", "场景", "超限"],
            [
                [c.get("index"), f"{c.get('start_s')}-{c.get('end_s')}s",
                 f"{c.get('duration_s')}s",
                 "、".join(str(q) for q in c.get("shot_seqs") or []),
                 "、".join(c.get("scenes") or []),
                 "⚠ 超" if c.get("over_limit") else ""]
                for c in chunks
            ],
        )
        for c in chunks:
            md.h3(f"段 {c.get('index')}({c.get('start_s')}-{c.get('end_s')}s)")
            md.add("**视频提示词(文生视频)**", "", c.get("motion_prompt_cn") or "(未生成)", "")
            md.add("**英文视频提示词**", "", c.get("motion_prompt_en") or "(未生成)", "")
            md.add(f"**首帧指引**:{c.get('first_frame_hint') or '(用本段首格静帧)'}", "")
            md.add(f"**拼接提示**:{c.get('link_note') or '(硬切)'}", "")

    pack = plan.pack or {}
    if pack.get("checklist"):
        totals = pack.get("totals") or {}
        md.h2("成片包")
        md.add(
            f"镜头 {totals.get('shots', '?')} 格 | 分镜总时长 {totals.get('storyboard_s', '?')}s"
            f"(目标 {totals.get('target_s', '?')}s) | 解说估时 {totals.get('voice_s', '?')}s"
        )
        md.add("")
        md.table(
            ["#", "场景", "秒", "字幕", "转场", "配乐", "备注"],
            [
                [c.get("seq"), c.get("scene"), f"{c.get('duration_s')}s",
                 c.get("subtitle"), c.get("transition"), c.get("bgm_tag"), c.get("note")]
                for c in pack["checklist"]
            ],
        )
        if pack.get("narration_full"):
            md.h2("整段解说(粘给 TTS 一把梭)")
            md.add(str(pack["narration_full"]), "")
        md.quote(
            "出片顺序:分镜提示词出图 → 图生视频/加轻动 → 解说词配音 → 按剪辑清单拼接"
            " → 压 SRT 字幕 → 铺 BGM → 收束加 slogan 字幕。",
            blank=True,
        )
        # 音频三轨说明:段视频提示词里那句「不要人声」是分轨,不是静音(口径见 media.audio)
        md.extend(audio_track_note())
    return md.text()


def export_json(plan: PromoPlan, shots: list[PromoShot]) -> str:
    from app.engines.promo.common import plan_dict, shot_dict

    payload = {
        "plan": {k: v for k, v in plan_dict(plan).items()},
        "shots": [shot_dict(s) for s in shots],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
