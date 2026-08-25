# app/engines/birthday/exporter.py
# -*- coding: utf-8 -*-
"""生日祝福导出:手卡 Markdown / SRT 字幕 / JSON。时间轴走 media 的 SRT 内核。

手卡比情绪短片多两块生日专属物料:
- 寿星资料卡(含回忆点清单)——出片时贴在旁边,逐条核对有没有被落实;
- 分段出片玩法指引——借鉴开源 ai-video-generation 类 skill 的能力分类法
  (文生视频/图生视频/对口型),告诉用户回忆杀段该走哪条工具路径。
"""
from __future__ import annotations

import json

from app.db.models import BirthdayWish
from app.engines.birthday.common import pack_label, relationship_label, tone_display, wish_dict
from app.engines.media.audio import audio_track_note
from app.engines.media.subtitles import srt_from_rows

# 分段出片玩法指引(手卡与导出共用一段文案):能力分类参考开源
# inference.sh ai-video-generation skill 的 t2v / i2v / lipsync 分类,落到本站口径。
PLAYGUIDE_LINES = [
    "> **出片玩法指引**(按段选工具,一段一次生成):",
    "> ① 普通段:复制段提示词 → 即梦/可灵/minimax 文生视频;",
    "> ② 回忆杀段:传**寿星真实照片**当参考图走图生视频(提示词已含「保持参考照片"
    "面部特征与体型」,不写这句脸会漂);没有合适照片就先按定妆卡文生图;",
    "> ③ 想让寿星照片开口说祝福:该句台词不进视频提示词,拿照片+台词走对口型工具"
    "(即梦对口型一类)单独做,成片在剪辑时插入;",
    "> ④ 全部段落出完 → 剪映画布拼接 → 压 SRT → 末格加祝福金句字卡。",
]

# BGM 建议(按基调):只给风格描述不点版权曲名,与全站口径一致
_BGM_BY_TONE = {
    "prank": "滑稽拨弦/俏皮爵士,反转前一拍静音",
    "tearjerk": "单钢琴弱起,回忆段才进弦乐",
    "warm": "轻快尤克里里/木吉他指弹",
    "surprise": "前半段几乎无配乐(冷),引爆时鼓点齐进",
    "hype": "鼓点递进的热血电子/摇滚,收在重拍定格",
    "cute": "八音盒/玩具钢琴,奶乎乎的节奏",
}


def export_srt(row: BirthdayWish) -> str:
    clip = row.clip or {}
    return srt_from_rows(clip.get("shots") or [])


def export_markdown(row: BirthdayWish) -> str:
    clip = row.clip or {}
    shots = clip.get("shots") or []
    name = f"{row.honoree_name or '寿星'}的生日片 · {clip.get('take', '')}"
    L: list[str] = []
    L.append(f"# 生日祝福手卡 · {name}")
    L.append("")
    L.append(
        f"- 基调:{tone_display(row)} | 时长:{row.duration_s}s | "
        f"画风:{pack_label(row.pack or '') or (row.style_name or row.direction)}"
        f" | 分镜 {len(shots)} 格 · {sum(int(s.get('duration_s') or 0) for s in shots)}s"
    )
    if clip.get("logline"):
        L.append(f"- 本子:{clip['logline']}")
    if clip.get("emotion_curve"):
        L.append(f"- 情绪曲线:{clip['emotion_curve']}")
    if clip.get("hook_text"):
        L.append(f"- 开场钩子:{clip['hook_text']}")
    if clip.get("punchline"):
        L.append(f"- **祝福金句字幕卡:{clip['punchline']}**")
    L.append("")
    # 寿星资料卡:出片时贴在旁边逐条核对(定制片的施工基准,不只是备忘)
    L.append("## 寿星资料卡")
    L.append("")
    L.append(f"- 称呼:**{row.honoree_name or '(未填)'}** | 关系:{relationship_label(row.relationship)}")
    if row.milestone:
        L.append(f"- 里程碑:{row.milestone}")
    L.append(f"- 送出人:{row.sender_desc or '(未说明)'}")
    memories = [str(m) for m in (row.memories or []) if str(m or "").strip()]
    if memories:
        L.append("- 回忆点(逐条核对是否被分镜落实):")
        for i, m in enumerate(memories, 1):
            L.append(f"  {i}. {m}")
    L.append("")
    if clip.get("cautions"):
        L.append(f"> ⚠ 需核实:{';'.join(clip['cautions'])}")
        L.append("")
    if row.style_cn:
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
    cards = clip.get("character_cards") or []
    if cards:
        L.append("## 角色定妆卡(参考图用)")
        L.append("")
        L.append("> 复制每张卡的描述去文生图出定妆图,再上传作参考图,人物才不会漂;"
                 "寿星本人优先用真实照片(走图生视频)。")
        L.append("")
        for c in cards:
            L.append(f"- **{c.get('name', '')}**:{c.get('desc', '')}")
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
        L.extend(PLAYGUIDE_LINES)
        L.append("")
        bgm = _BGM_BY_TONE.get(row.tone, "按基调自选:整片铺一条,段边界不换曲")
        L.append(f"> **BGM 风格建议**:{bgm}(整片一条,分段自带音乐在接缝处必错拍)。")
        L.append("")
        # 音频口径:整片一段生成完时模型自带音频直接可用(口径见 media.audio)
        L.extend(audio_track_note(single_segment=len(chunks) <= 1))
    return "\n".join(L)


def export_json(row: BirthdayWish) -> str:
    return json.dumps(wish_dict(row), ensure_ascii=False, indent=2)
