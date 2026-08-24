# app/engines/promo/pack.py
# -*- coding: utf-8 -*-
"""宣传片成片包:配音稿(解说词逐镜对位 + 估时)+ 剪辑清单(转场/配乐标注)。

确定性部分(时间轴/估时/整段口播)代码算,LLM 只做转场与配乐情绪标注。
结构对齐漫剧成片包(dubbing/checklist/totals),前端与导出复用同一形态。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.text import clip
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_PACK_PROMPT

_CHARS_PER_SEC = 4.0  # 宣传片解说词语速略慢于剧情对白


class PromoPackError(ValueError):
    """成片包的业务性错误(信息直接上屏)。"""


async def build_pack(db: Session, plan: PromoPlan, progress=lambda s: None) -> dict:
    shots = (
        db.query(PromoShot).filter(PromoShot.promo_id == plan.id).order_by(PromoShot.seq).all()
    )
    if not shots:
        raise PromoPackError("还没有分镜,先「拆分镜」再出成片包。")

    # ---- LLM 标注:转场 + 配乐(一次批量) ----
    progress(f"AI 正在标注后期(转场/配乐,{len(shots)} 格)…")
    shots_block = "\n".join(
        f"- seq {s.seq}|{s.shot_type}/{s.duration_s}s|场景:{s.scene_name or '未指定'}|画面:{s.action_desc}"
        for s in shots
    )
    adapter = get_adapter_for(Task.PROMO_PACK, timeout=300)
    prompt = PROMO_PACK_PROMPT.format(duration_s=plan.duration_s, shots_block=shots_block)
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    ann: dict[int, dict] = {}
    for item in (data.get("shots") or []):
        if isinstance(item, dict) and item.get("seq") is not None:
            try:
                ann[int(item["seq"])] = item
            except (TypeError, ValueError):
                continue

    # ---- 配音稿(确定性:解说词逐镜对位 + 估时) ----
    dubbing = []
    narration = []
    for s in shots:
        text = (s.dialogue or "").strip()
        if not text:
            continue
        est_s = max(1, round(len(text) / _CHARS_PER_SEC))
        dubbing.append(
            {
                "seq": s.seq,
                "speaker": "旁白",
                "voice": "旁白声线(沉稳大气,语速从容)",
                "tts_hint": "剪映:选「纪录片解说/大气男声」类;火山:沉稳中年男声;MiniMax 按同方向挑",
                "text": text,
                "tts_text": text,
                "est_s": est_s,
                "shot_duration_s": s.duration_s,
            }
        )
        narration.append(text)

    # ---- 剪辑清单 ----
    checklist = []
    for s in shots:
        a = ann.get(s.seq, {})
        note = ""
        d = next((x for x in dubbing if x["seq"] == s.seq), None)
        if d is not None:
            diff = d["est_s"] - s.duration_s
            if diff >= 2:
                note = f"解说比画面长约{diff}s:延长镜头或提语速"
            elif diff <= -3:
                note = "画面比解说长:补空镜/停顿"
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
        "dubbing": dubbing,
        "narration_full": "\n".join(narration),
        "checklist": checklist,
        "totals": {
            "shots": len(shots),
            "target_s": plan.duration_s,
            "storyboard_s": sum(s.duration_s for s in shots),
            "voice_s": sum(d["est_s"] for d in dubbing),
        },
    }
    plan.pack = pack
    db.commit()
    return {"pack": pack}
