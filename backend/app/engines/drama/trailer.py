# app/engines/drama/trailer.py
# -*- coding: utf-8 -*-
"""预告片:从各集高能素材(钩子/卡点/高能分镜)重剪一条 30-60 秒宣传片。

与正片管线同一套一致性纪律:画风锚 + 角色锚逐字注入每条提示词,
LLM 漏了引擎兜底强制拼入(dict 版的注入检查,行对象版见 prompt_render)。
一次 LLM 出全部产物(选材/旁白/分镜/三轨提示词)——预告片体量小,
单次调用反而利于全片节奏统一。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaShot,
    DramaStyleCard,
    DramaTrailer,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    character_anchor_maps,
    clip,
    coerce_int,
    match_character,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import TRAILER_PROMPT

_MAX_LINES = 12
_MAX_SHOTS = 15
_MIN_SHOTS = 4
# 预告片每集进素材清单的高能分镜条数
_TOP_SHOTS_PER_EP = 5


class DramaTrailerError(ValueError):
    """预告片的业务性错误(信息直接上屏)。"""


def _episodes_digest(db: Session, project_id: int, from_ep: int, to_ep: int) -> list[DramaEpisode]:
    eps = (
        db.query(DramaEpisode)
        .filter(
            DramaEpisode.project_id == project_id,
            DramaEpisode.ep_index >= from_ep,
            DramaEpisode.ep_index <= to_ep,
        )
        .order_by(DramaEpisode.ep_index)
        .all()
    )
    return eps


def _ep_block(db: Session, ep: DramaEpisode) -> str:
    lines = [
        f"【第 {ep.ep_index} 集《{ep.title}》】",
        f"  钩子:{ep.hook or '(无)'}",
        f"  梗概:{ep.recap or '(无)'}",
        f"  卡点:{ep.cliffhanger or '(无)'}",
    ]
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == ep.id)
        .order_by(DramaShot.seq)
        .limit(_TOP_SHOTS_PER_EP)
        .all()
    )
    for s in shots:
        line = f"  分镜{s.seq}:{s.action_desc}"
        if s.dialogue:
            line += f"|台词:「{s.dialogue}」"
        lines.append(line)
    return "\n".join(lines)


def _ensure_anchor(prompt: str, anchor: str, prefix: str) -> str:
    if not anchor or anchor in prompt:
        return prompt
    return f"{prefix}{anchor}。{prompt}"


def _ensure_char_anchors(shot: dict, prompt_cn: str, by_name, by_alias) -> str:
    missing = []
    for name in shot.get("characters") or []:
        card = match_character(str(name), by_name, by_alias)
        if card and card.appearance_cn and card.appearance_cn not in prompt_cn and card.name not in prompt_cn:
            missing.append(f"{card.name}:{card.appearance_cn}")
    if not missing:
        return prompt_cn
    return "【角色锚】" + ";".join(missing) + "。" + prompt_cn


def _normalize_shots(
    raw: list, style: DramaStyleCard, by_name, by_alias
) -> list[dict]:
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        prompt_cn = clip(item.get("prompt_cn"), 1000)
        prompt_en = clip(item.get("prompt_en"), 700)
        negative = clip(item.get("negative"), 500)
        prompt_cn = _ensure_anchor(prompt_cn, style.style_cn, "【画风锚】")
        prompt_en = _ensure_anchor(prompt_en, style.style_en, "")
        shot = {
            "seq": len(out) + 1,
            "source_ep": coerce_int(item.get("source_ep"), 0, lo=0),
            "scene_name": clip(item.get("scene_name"), 200),
            "characters": [
                str(c).strip() for c in (item.get("characters") or []) if str(c or "").strip()
            ][:6],
            "action_desc": action[:300],
            "shot_type": clip(item.get("shot_type"), 20),
            "camera": clip(item.get("camera"), 20),
            "dialogue": clip(item.get("dialogue"), 400),
            "duration_s": coerce_int(item.get("duration_s"), 3, lo=1, hi=8),
            "prompt_cn": prompt_cn,
            "prompt_en": prompt_en,
            "negative": negative,
        }
        shot["prompt_cn"] = _ensure_char_anchors(shot, shot["prompt_cn"], by_name, by_alias)
        if style.negative and style.negative not in shot["negative"]:
            shot["negative"] = (
                f"{style.negative},{shot['negative']}" if shot["negative"] else style.negative
            )
        out.append(shot)
        if len(out) >= _MAX_SHOTS:
            break
    return out


async def generate_trailer(
    db: Session,
    project: Project,
    from_ep: int,
    to_ep: int,
    target_s: int,
    progress=lambda s: None,
) -> dict:
    style = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project.id)
        .first()
    )
    if style is None or not (style.style_cn or style.style_en):
        raise DramaTrailerError("先生成「美术风格卡」,预告片的画风统一靠它。")

    eps = _episodes_digest(db, project.id, from_ep, to_ep)
    if not eps:
        raise DramaTrailerError("所选范围没有已规划的集,先「切集」再做预告片。")

    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .order_by(DramaCharacterCard.id)
        .all()
    )
    char_lines = [
        f"【{c.name}】{c.appearance_cn}\n  EN: {c.appearance_en}" for c in cards
    ] or ["(暂无角色卡,按素材人名自行合理设计人物)"]

    progress(f"AI 正在从 {len(eps)} 集素材里混剪预告片({target_s}s)…")
    adapter = get_adapter_for(Task.DRAMA_TRAILER, timeout=300)
    prompt = TRAILER_PROMPT.format(
        target_s=target_s,
        title=project.title,
        episodes_block="\n".join(_ep_block(db, ep) for ep in eps),
        style_cn=style.style_cn,
        style_en=style.style_en,
        style_negative=style.negative,
        character_anchors="\n".join(char_lines),
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    by_name, by_alias = character_anchor_maps(db, project.id)
    shots = _normalize_shots(data.get("shots") or [], style, by_name, by_alias)
    if len(shots) < _MIN_SHOTS:
        raise DramaTrailerError(
            f"预告片镜头过少({len(shots)}/{_MIN_SHOTS}),请重试——素材可能太薄,先补几集分镜更好。"
        )
    lines_out = []
    for item in (data.get("lines") or []):
        if not isinstance(item, dict):
            continue
        text = clip(item.get("text"), 200)
        if text:
            lines_out.append(
                {"speaker": clip(item.get("speaker"), 60) or "旁白", "text": text}
            )
        if len(lines_out) >= _MAX_LINES:
            break

    trailer = {
        "target_s": target_s,
        "title": clip(data.get("title"), 200),
        "lines": lines_out,
        "shots": shots,
        "totals": {
            "shots": len(shots),
            "duration_s": sum(s["duration_s"] for s in shots),
            "from_ep": from_ep,
            "to_ep": to_ep,
        },
    }
    row = (
        db.query(DramaTrailer).filter(DramaTrailer.project_id == project.id).first()
    )
    if row is None:
        row = DramaTrailer(project_id=project.id)
        db.add(row)
    row.target_s = target_s
    row.title = trailer["title"]
    row.lines = trailer["lines"]
    row.shots = trailer["shots"]
    db.commit()
    return trailer
