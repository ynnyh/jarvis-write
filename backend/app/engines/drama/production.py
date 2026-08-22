# app/engines/drama/production.py
# -*- coding: utf-8 -*-
"""成片包(阶段 2):一集的配音稿 + 剪辑清单,拿去 TTS + 剪映就能拼成片。

分工原则:
- 确定性部分(时间轴/字幕/估时/声线匹配)由代码算——可测、可复现、零成本;
- 创作部分(朗读润色 tts_text/转场/配乐情绪标记)一次 LLM 批量标注。
配音估时按中文 TTS 约 4.5 字/秒,和分镜时长并排给出,长短不匹配在 note 里提示,
剪辑时照着调镜头长度或语速。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaProductionPack,
    DramaShot,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    MODE_DESC,
    character_anchor_maps,
    clip,
    episode_dict,
    match_character,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import PRODUCTION_PACK_PROMPT

# 中文 TTS 语速基准(字/秒):旁白类偏慢,对白类中速,取中间值
_CHARS_PER_SEC = 4.5


class DramaPackError(ValueError):
    """成片包的业务性错误(信息直接上屏)。"""


def _match_speaker(dialogue: str, lines: list[dict]) -> str:
    """台词 → 说话人:先精确匹配剧本 line 文本,再双向包含模糊兜底。"""
    text = (dialogue or "").strip()
    if not text:
        return ""
    for l in lines:
        if str(l.get("text", "")).strip() == text:
            return str(l.get("speaker", "")).strip()
    for l in lines:  # 模糊:分镜台词可能截断或加了语气词
        lt = str(l.get("text", "")).strip()
        if len(lt) >= 6 and (lt in text or text in lt):
            return str(l.get("speaker", "")).strip()
    return ""


def _shots_block(shots: list[DramaShot]) -> str:
    rows = []
    for s in shots:
        rows.append(
            f"- seq {s.seq}|{s.shot_type}/{s.camera}/{s.duration_s}s"
            f"|场景:{s.scene_name or '未指定'}\n"
            f"  画面:{s.action_desc}\n"
            f"  台词:{s.dialogue or '(无)'}"
        )
    return "\n".join(rows)


async def build_production_pack(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.seq)
        .all()
    )
    if not shots:
        raise DramaPackError("这一集还没有分镜,先「拆分镜」再出成片包。")

    script = episode.script or {}
    lines = script.get("lines") if isinstance(script, dict) else None
    lines = [l for l in (lines or []) if isinstance(l, dict)]

    # ---- LLM 后期标注(一次批量):朗读润色 + 转场 + 配乐情绪 ----
    progress(f"AI 正在标注后期(朗读/转场/配乐,{len(shots)} 格)…")
    adapter = get_adapter_for(Task.DRAMA_PACK, timeout=300)
    prompt = PRODUCTION_PACK_PROMPT.format(
        ep_index=episode.ep_index,
        ep_title=episode.title,
        mode_desc=MODE_DESC.get(episode.mode, MODE_DESC["dialogue"]),
        duration_target_s=episode.duration_target_s,
        shots_block=_shots_block(shots),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    ann: dict[int, dict] = {}
    for item in (data.get("shots") or []):
        if isinstance(item, dict) and item.get("seq") is not None:
            try:
                ann[int(item["seq"])] = item
            except (TypeError, ValueError):
                continue

    char_by_name, char_by_alias = character_anchor_maps(db, project.id)

    # ---- 配音稿(确定性:说话人匹配 + 声线映射 + 估时) ----
    dubbing: list[dict] = []
    narration_lines: list[str] = []
    for s in shots:
        if not (s.dialogue or "").strip():
            continue
        speaker = _match_speaker(s.dialogue, lines)
        if not speaker:
            speaker = "旁白" if episode.mode == "narration" else "待定"
        card = None
        if speaker not in ("旁白", "待定"):
            card = match_character(speaker, char_by_name, char_by_alias)
        text = s.dialogue.strip()
        tts_text = clip(ann.get(s.seq, {}).get("tts_text"), 400) or text
        est_s = max(1, round(len(text) / _CHARS_PER_SEC))
        dubbing.append(
            {
                "seq": s.seq,
                "speaker": speaker,
                "voice": card.voice_desc if card else ("旁白声线(沉稳中性)" if speaker == "旁白" else ""),
                "tts_hint": card.tts_hint if card else "",
                "reading_notes": card.reading_notes if card else "",
                "text": text,
                "tts_text": tts_text,
                "est_s": est_s,
                "shot_duration_s": s.duration_s,
            }
        )
        if speaker == "旁白":
            narration_lines.append(tts_text)

    # ---- 剪辑清单(确定性时间轴 + LLM 标注) ----
    checklist: list[dict] = []
    for s in shots:
        a = ann.get(s.seq, {})
        note = ""
        d = next((x for x in dubbing if x["seq"] == s.seq), None)
        if d is not None:
            diff = d["est_s"] - s.duration_s
            if diff >= 2:
                note = f"配音比画面长约{diff}s:延长镜头或提语速"
            elif diff <= -3:
                note = "画面比配音长:补停顿/环境音/反应镜头"
        checklist.append(
            {
                "seq": s.seq,
                "scene": s.scene_name,
                "duration_s": s.duration_s,
                "subtitle": (s.dialogue or "").strip(),
                "transition": clip(a.get("transition"), 40) or "硬切",
                "bgm_tag": clip(a.get("bgm_tag"), 40),
                "note": note,
            }
        )

    pack = {
        "mode": episode.mode,
        "synopsis": clip(script.get("synopsis"), 300) if isinstance(script, dict) else "",
        "dubbing": dubbing,
        "narration_full": "\n".join(narration_lines),
        "checklist": checklist,
        "totals": {
            "shots": len(shots),
            "target_s": episode.duration_target_s,
            "storyboard_s": sum(s.duration_s for s in shots),
            "voice_s": sum(d["est_s"] for d in dubbing),
        },
    }

    row = (
        db.query(DramaProductionPack)
        .filter(DramaProductionPack.episode_id == episode.id)
        .first()
    )
    if row is None:
        row = DramaProductionPack(episode_id=episode.id)
        db.add(row)
    row.pack = pack
    db.commit()
    return {"episode": episode_dict(episode), "pack": pack}
