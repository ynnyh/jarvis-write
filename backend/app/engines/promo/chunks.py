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
from app.engines.drama.common import clip
from app.llm.router import Task, get_adapter_for
from app.prompts.promo import PROMO_CHUNKS_PROMPT

VALID_CHUNK_S = (5, 10, 15)


class PromoChunkError(ValueError):
    """切段的业务性错误(信息直接上屏)。"""


def _group_shots(shots: list[PromoShot], chunk_s: int) -> list[list[PromoShot]]:
    """镜头边界贪心聚段:装得下就装,装不下开新段;单镜头超限独立成段。"""
    groups: list[list[PromoShot]] = []
    cur: list[PromoShot] = []
    cur_s = 0
    for s in shots:
        if cur and cur_s + s.duration_s > chunk_s:
            groups.append(cur)
            cur, cur_s = [], 0
        cur.append(s)
        cur_s += s.duration_s
    if cur:
        groups.append(cur)
    return groups


def _ensure_anchor(prompt: str, anchor: str, prefix: str) -> str:
    if not anchor or anchor in prompt:
        return prompt
    return f"{prefix}{anchor}。{prompt}"


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

    groups = _group_shots(shots, chunk_s)
    # 时间码(与 SRT/剪辑清单同一累计轴)
    start_s: list[int] = []
    t = 0
    for g in groups:
        start_s.append(t)
        t += sum(s.duration_s for s in g)

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
    for i, g in enumerate(groups, start=1):
        dur = sum(s.duration_s for s in g)
        a = ann.get(i, {})
        motion_cn = clip(a.get("motion_prompt_cn"), 800)
        motion_en = clip(a.get("motion_prompt_en"), 600)
        # 画风锚兜底(与三轨提示词同纪律)
        motion_cn = _ensure_anchor(motion_cn, plan.style_cn, "【画风锚】")
        motion_en = _ensure_anchor(motion_en, plan.style_en, "")
        items.append(
            {
                "index": i,
                "start_s": start_s[i - 1],
                "end_s": start_s[i - 1] + dur,
                "duration_s": dur,
                "over_limit": dur > chunk_s,
                "shot_seqs": [s.seq for s in g],
                "scenes": [s.scene_name for s in g if s.scene_name],
                "subtitle": "\n".join(s.dialogue for s in g if s.dialogue),
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
