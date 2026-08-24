# app/engines/promo/chunks.py
# -*- coding: utf-8 -*-
"""生成切段:按镜头边界贪心聚段(每段 ≤ chunk_s 秒),一段一次文生视频/图生视频,画布拼接。

确定性部分:切段边界(镜头边界贪心,绝不在镜头中间断)、起止时间码(与 SRT 同轴)、
字幕对位、包含镜头。LLM 只写每段的视频运动提示词与拼接指引(一次批量)。
单镜头超过 chunk_s 时独立成段(超限标注)——生成侧可对该段降速/重复生成拼接。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PromoPlan, PromoShot
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.anchors import ensure_style_anchors
from app.engines.media.audio import ensure_audio_rules
from app.engines.media.segments import chunk_rows, group_by_limit
from app.engines.media.text import clip
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_CHUNKS_PROMPT

VALID_CHUNK_S = (5, 10, 15)


class PromoChunkError(ValueError):
    """切段的业务性错误(信息直接上屏)。"""


def _chunks_block(groups: list[list[PromoShot]], start_s: list[int]) -> str:
    rows = []
    for i, g in enumerate(groups):
        subs = ";".join(f"镜头{s.seq}({s.duration_s}s,{s.shot_type}/{s.camera}):{s.action_desc}" for s in g)
        dia = ";".join(s.dialogue for s in g if s.dialogue) or "(无解说)"
        rows.append(
            f"- 段{i + 1}|时间码 {start_s[i]}-{start_s[i] + sum(s.duration_s for s in g)}s"
            f"|共 {sum(s.duration_s for s in g)}s\n  {subs}\n  解说对位:{dia}"
        )
    return "\n".join(rows)


async def build_chunks(
    db: Session, plan: PromoPlan, chunk_s: int = 15, progress=lambda s: None
) -> dict:
    if chunk_s not in VALID_CHUNK_S:
        raise PromoChunkError(f"切段时长只支持 {'/'.join(str(x) for x in VALID_CHUNK_S)} 秒。")
    shots = (
        db.query(PromoShot).filter(PromoShot.promo_id == plan.id).order_by(PromoShot.seq).all()
    )
    if not shots:
        raise PromoChunkError("还没有分镜,先「拆分镜」再切段。")

    groups = group_by_limit(shots, chunk_s)
    # 确定性部分(时间码/超限标注/字幕对位)全部由 media.segments 出,与 SRT 同一累计轴
    rows = chunk_rows(groups, chunk_s)
    start_s = [r["start_s"] for r in rows]

    # ---- LLM 批量:每段视频提示词 + 首帧指引 + 拼接提示 ----
    progress(f"AI 正在写分段视频提示词({len(groups)} 段,每段 ≤{chunk_s}s)…")
    adapter = get_adapter_for(Task.PROMO_CHUNKS, timeout=300)
    prompt = PROMO_CHUNKS_PROMPT.format(
        chunk_s=chunk_s,
        style_cn=plan.style_cn or "(未生成风格卡——先「生成视觉风格」再切段)",
        style_en=plan.style_en or "",
        chunks_block=_chunks_block(groups, start_s),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    ann: dict[int, dict] = {}
    for item in (data.get("chunks") or []):
        if isinstance(item, dict) and item.get("index") is not None:
            try:
                ann[int(item["index"])] = item
            except (TypeError, ValueError):
                continue

    items = []
    for row, g in zip(rows, groups):
        a = ann.get(row["index"], {})
        # 画风锚兜底(与三轨提示词同纪律,口径见 media.anchors)
        motion_cn, motion_en = ensure_style_anchors(
            clip(a.get("motion_prompt_cn"), 800),
            clip(a.get("motion_prompt_en"), 600),
            plan.style_cn,
            plan.style_en,
        )
        # 音频分轨兜底:段视频只出环境音,人声与 BGM 后期整片铺(口径与理由见 media.audio)。
        # 不加这句,Veo 一类会给每段自己编一版对白/配乐,拼起来两层人声、音乐错拍。
        motion_cn, motion_en = ensure_audio_rules(motion_cn, motion_en)
        items.append(
            {
                **row,
                "motion_prompt_cn": motion_cn,
                "motion_prompt_en": motion_en,
                "first_frame_hint": clip(a.get("first_frame_hint"), 200)
                or f"用镜头{g[0].seq}的静帧作首帧",
                "link_note": clip(a.get("link_note"), 200),
            }
        )

    result = {"chunk_s": chunk_s, "items": items}
    plan.chunks = result
    db.commit()
    return {"chunks": result}
