# app/engines/drama/prompt_render.py
# -*- coding: utf-8 -*-
"""分镜三轨提示词:画风锚 + 角色锚 + 场景锚注入每一格(中文六层/英文/负面)。

一致性双保险:
1. 提示词要求锚段「逐字保留」;
2. 引擎兜底检查——LLM 漏了画风锚/角色锚,确定性前置拼接,保证注入永不落空。
分块调用(每块 8 格)防单次输出过长,块内风格上下文一致。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaEpisode, DramaShot, DramaStyleCard, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    character_anchor_maps,
    clip,
    episode_dict,
    match_character,
    scene_anchor_map,
    shot_dict,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import SHOT_PROMPT_PROMPT

_CHUNK = 8


class DramaPromptError(ValueError):
    """提示词渲染的业务性错误(信息直接上屏)。"""


def _shots_block(shots: list[DramaShot]) -> str:
    lines = []
    for s in shots:
        who = "、".join(s.characters or []) or "(无角色)"
        lines.append(
            f"- seq {s.seq}|场景:{s.scene_name or '(未指定)'}|角色:{who}"
            f"|景别:{s.shot_type}|运镜:{s.camera}|时长:{s.duration_s}s\n"
            f"  画面:{s.action_desc}\n"
            f"  台词:{s.dialogue or '(无)'}"
        )
    return "\n".join(lines)


def _anchor_blocks(
    shots: list[DramaShot], char_by_name, char_by_alias, scene_by_name
) -> tuple[str, str]:
    """收集本块镜头实际用到的角色/场景锚段,渲染成提示词块(中英一起给)。"""
    chars: dict[str, object] = {}
    scenes: dict[str, object] = {}
    for s in shots:
        for name in s.characters or []:
            card = match_character(name, char_by_name, char_by_alias)
            if card is not None:
                chars[card.name] = card
        if s.scene_name:
            card = scene_by_name.get(s.scene_name)
            if card is not None:
                scenes[card.name] = card

    char_lines = [
        f"【{c.name}】{c.appearance_cn}\n  EN: {c.appearance_en}" for c in chars.values()
    ] or ["(本块镜头无角色卡命中,按分镜描述自行合理设计人物)"]
    scene_lines = [
        f"【{sc.name}】{sc.appearance_cn}\n  EN: {sc.appearance_en}"
        for sc in scenes.values()
    ] or ["(本块镜头无场景卡命中,按分镜场景名自行合理设计环境)"]
    return "\n".join(char_lines), "\n".join(scene_lines)


def _ensure_anchor(shot_prompt: str, anchor: str, prefix: str) -> str:
    """锚段兜底:LLM 输出里没含锚段时,确定性前置拼接。"""
    if not anchor or anchor in shot_prompt:
        return shot_prompt
    return f"{prefix}{anchor}。{shot_prompt}"


def _ensure_character_anchors(
    shot: DramaShot, prompt_cn: str, char_by_name, char_by_alias
) -> str:
    """角色锚兜底:出场角色的锁定外貌段缺失时前置补齐(每角色一次)。"""
    missing = []
    for name in shot.characters or []:
        card = match_character(name, char_by_name, char_by_alias)
        if card and card.appearance_cn and card.appearance_cn not in prompt_cn and card.name not in prompt_cn:
            missing.append(f"{card.name}:{card.appearance_cn}")
    if not missing:
        return prompt_cn
    return "【角色锚】" + ";".join(missing) + "。" + prompt_cn


async def render_shot_prompts(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    style = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project.id)
        .first()
    )
    if style is None or not (style.style_cn or style.style_en):
        raise DramaPromptError("先生成「美术风格卡」,全片画风统一靠它。")

    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.seq)
        .all()
    )
    if not shots:
        raise DramaPromptError("这一集还没有分镜,先「拆分镜」再出提示词。")

    char_by_name, char_by_alias = character_anchor_maps(db, project.id)
    scene_by_name = scene_anchor_map(db, project.id)

    adapter = get_adapter_for(Task.DRAMA_PROMPT, timeout=300)
    total = len(shots)
    for start in range(0, total, _CHUNK):
        chunk = shots[start : start + _CHUNK]
        progress(f"AI 正在出提示词({start + 1}-{min(start + _CHUNK, total)}/{total} 格)…")
        char_anchor, scene_anchor = _anchor_blocks(
            chunk, char_by_name, char_by_alias, scene_by_name
        )
        prompt = SHOT_PROMPT_PROMPT.format(
            style_cn=style.style_cn,
            style_en=style.style_en,
            style_negative=style.negative,
            character_anchors=char_anchor,
            scene_anchors=scene_anchor,
            shots_block=_shots_block(chunk),
        )
        raw = await adapter.ask(prompt)
        data = parse_llm_json(raw)
        by_seq: dict[int, dict] = {}
        for item in (data.get("shots") or []):
            if isinstance(item, dict) and item.get("seq") is not None:
                try:
                    by_seq[int(item["seq"])] = item
                except (TypeError, ValueError):
                    continue

        for shot in chunk:
            item = by_seq.get(shot.seq) or {}
            prompt_cn = clip(item.get("prompt_cn"), 1200)
            prompt_en = clip(item.get("prompt_en"), 800)
            negative = clip(item.get("negative"), 500)
            if not prompt_cn and not prompt_en:
                continue  # 该格 LLM 漏输出:保留旧提示词,不写空
            # 兜底注入:画风锚 + 角色锚(中文);英文画风锚;负面词基座
            prompt_cn = _ensure_anchor(prompt_cn, style.style_cn, "【画风锚】")
            prompt_cn = _ensure_character_anchors(shot, prompt_cn, char_by_name, char_by_alias)
            prompt_en = _ensure_anchor(prompt_en, style.style_en, "")
            if style.negative and style.negative not in negative:
                negative = f"{style.negative},{negative}" if negative else style.negative
            shot.prompt_cn = prompt_cn
            shot.prompt_en = prompt_en
            shot.negative = negative

    if all(s.prompt_cn or s.prompt_en for s in shots):
        episode.status = "ready"
    db.commit()
    return {
        "episode": episode_dict(episode),
        "shots": [shot_dict(s) for s in shots],
    }
