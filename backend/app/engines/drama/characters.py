# app/engines/drama/characters.py
# -*- coding: utf-8 -*-
"""角色视觉卡 + 场景定调卡(资产层,借鉴 AnimaHub 素材库)。

角色卡从故事圣经 Entity 批量生成;locked 的卡重跑时跳过不覆盖
(AI-Novel-Writing-Assistant 的待确认/锁定机制)。场景卡从蓝图 scene_location
与圣经 location 实体归纳。一个 job 两次 LLM 调用。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaSceneCard,
    Entity,
    Fact,
    Outline,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    character_card_dict,
    clip,
    concept_block,
    scene_card_dict,
    style_card,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import CHARACTER_PROMPT, REF_SHEET_PROMPT, SCENE_PROMPT

# 单次批量上限:防提示词与输出失控(超出的角色下轮再生成)
_MAX_CHARACTERS = 12
_MAX_SCENES = 10
_MAX_REF_SHEETS = 8  # 定妆照提示词单次批量上限(每条比视觉卡更长)


class DramaAssetError(ValueError):
    """资产生成的业务性错误(信息直接上屏,如「圣经里还没有角色」)。"""


def _entity_digest(db: Session, entity: Entity) -> str:
    """角色档案摘要:base_profile 平铺 + 最近 4 条重要事实。"""
    profile = entity.base_profile if isinstance(entity.base_profile, dict) else {}
    parts = [f"{k}:{v}" for k, v in profile.items() if str(v or "").strip()]
    facts = (
        db.query(Fact.content)
        .filter(Fact.entity_id == entity.id)
        .order_by(Fact.valid_from.desc(), Fact.id.desc())
        .limit(4)
        .all()
    )
    parts.extend(str(c) for (c,) in facts if c)
    digest = ";".join(parts)
    return clip(digest, 300)


async def generate_character_cards(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """从故事圣经批量生成/更新角色卡(locked 跳过),返回 {cards, skipped_locked}。"""
    entities = (
        db.query(Entity)
        .filter(
            Entity.project_id == project.id,
            Entity.entity_type == "character",
            Entity.retired.is_(False),
        )
        .order_by(Entity.id)
        .limit(_MAX_CHARACTERS)
        .all()
    )
    if not entities:
        raise DramaAssetError(
            "故事圣经里还没有角色——先在写作区定稿几章让引擎抽取角色,再来生成角色卡。"
        )

    lines = []
    for e in entities:
        aliases = "/".join(str(a) for a in (e.aliases or []))
        head = f"{e.name}" + (f"(又称:{aliases})" if aliases else "")
        lines.append(f"【{head}】{_entity_digest(db, e)}")
    characters_block = "\n".join(lines)

    progress(f"AI 正在设计 {len(entities)} 张角色视觉卡…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = CHARACTER_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        characters_block=characters_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    # 既有卡索引:entity_id 优先,名字兜底
    existing = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .all()
    )
    by_entity = {c.entity_id: c for c in existing if c.entity_id}
    by_name = {c.name: c for c in existing}

    style = style_card(db, project.id)
    cards_out: list[dict] = []
    skipped_locked = 0
    entity_names = {e.name for e in entities}
    for item in (data.get("cards") or []):
        if not isinstance(item, dict):
            continue
        name = clip(item.get("name"), 200)
        if not name:
            continue
        target = by_entity.get(
            next((e.id for e in entities if e.name == name), None), None
        ) or by_name.get(name)
        if target is not None and target.locked:
            skipped_locked += 1
            cards_out.append(character_card_dict(target, style))
            continue
        if target is None:
            target = DramaCharacterCard(project_id=project.id, name=name)
            if name in entity_names:
                target.entity_id = next(e.id for e in entities if e.name == name)
            db.add(target)
        target.name = name
        target.appearance_cn = clip(item.get("appearance_cn"), 600)
        target.appearance_en = clip(item.get("appearance_en"), 400)
        target.outfit_cn = clip(item.get("outfit_cn"), 120)
        target.voice_desc = clip(item.get("voice_desc"), 120)
        cards_out.append(character_card_dict(target, style))

    db.commit()
    return {"cards": cards_out, "skipped_locked": skipped_locked}


def collect_scene_names(db: Session, project_id: int) -> list[str]:
    """场景名清单:蓝图 scene_location 为主,圣经 location 实体补充,去重限长。"""
    names: list[str] = []
    seen: set[str] = set()

    def _add(n: str) -> None:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    rows = (
        db.query(Outline.scene_location)
        .filter(
            Outline.project_id == project_id,
            Outline.scene_location.isnot(""),
        )
        .distinct()
        .all()
    )
    for (loc,) in rows:
        _add(str(loc or ""))
    ents = (
        db.query(Entity.name)
        .filter(
            Entity.project_id == project_id,
            Entity.entity_type == "location",
            Entity.retired.is_(False),
        )
        .all()
    )
    for (name,) in ents:
        _add(str(name or ""))
    return names[:_MAX_SCENES]


async def generate_scene_cards(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """为反复出现的场景批量生成定调卡;没有可归纳场景时静默返回空。"""
    names = collect_scene_names(db, project.id)
    if not names:
        return {"scenes": []}

    scenes_block = "\n".join(f"【{n}】" for n in names)
    progress(f"AI 正在为 {len(names)} 个场景定调…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = SCENE_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        scenes_block=scenes_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    existing = {
        c.name: c
        for c in db.query(DramaSceneCard)
        .filter(DramaSceneCard.project_id == project.id)
        .all()
    }
    out: list[dict] = []
    known = set(names)
    for item in (data.get("scenes") or []):
        if not isinstance(item, dict):
            continue
        name = clip(item.get("name"), 200)
        if not name or name not in known:
            continue  # LLM 自创场景不收,保持与蓝图对齐
        card = existing.get(name)
        if card is None:
            card = DramaSceneCard(project_id=project.id, name=name)
            db.add(card)
        card.appearance_cn = clip(item.get("appearance_cn"), 400)
        card.appearance_en = clip(item.get("appearance_en"), 300)
        out.append(scene_card_dict(card))

    db.commit()
    return {"scenes": out}


async def generate_assets(db: Session, project: Project, progress=lambda s: None) -> dict:
    """角色卡 + 场景卡一次跑完(对应前端「生成资产卡」按钮的 job)。"""
    chars = await generate_character_cards(db, project, progress)
    scenes = await generate_scene_cards(db, project, progress)
    return {"cards": chars["cards"], "skipped_locked": chars["skipped_locked"], **scenes}


# =============== 定妆照提示词(人物一致性:先出参考图) ===============

async def generate_ref_sheets(
    db: Session,
    project: Project,
    names: list[str] | None = None,
    progress=lambda s: None,
) -> dict:
    """为角色卡批量出「定妆照」提示词(先出一张参考图,后面每格拿它当参考图)。

    文字锚只能把漂移压小,压不到零——同一段外貌描述交给生图站,两次出图仍是两张脸。
    真正锁脸要靠「参考图 + 每格提示词」,所以这一步是出图前的第一步。

    names 显式给了就强制重出那几张(单卡「重出」按钮);不给则只补缺
    (ref_prompt_cn 为空的卡),避免整批覆盖用户手改过的提示词。
    """
    style = style_card(db, project.id)
    if style is None or not (style.style_cn or "").strip():
        raise DramaAssetError("还没有美术风格卡——先点「定画风」,定妆照要带画风锚才统一。")

    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .order_by(DramaCharacterCard.id)
        .all()
    )
    if not cards:
        raise DramaAssetError("还没有角色卡——先点「生成资产卡」,再出定妆照。")

    wanted = {str(n).strip() for n in (names or []) if str(n or "").strip()}
    if wanted:
        targets = [c for c in cards if c.name in wanted]
    else:
        targets = [c for c in cards if not (c.ref_prompt_cn or "").strip()]
    if not targets:
        # 全都有了:不空跑一次 LLM,把现状回给前端,让它提示「想改点单卡的重出」
        return {
            "cards": [character_card_dict(c, style) for c in cards],
            "generated": 0,
        }
    targets = targets[:_MAX_REF_SHEETS]

    cards_block = "\n".join(
        f"【{c.name}】锁定外貌:{clip(c.appearance_cn, 400)}"
        + (f";英文:{clip(c.appearance_en, 300)}" if c.appearance_en else "")
        + (f";标志服饰:{clip(c.outfit_cn, 120)}" if c.outfit_cn else "")
        for c in targets
    )
    progress(f"AI 正在写 {len(targets)} 张定妆照提示词…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = REF_SHEET_PROMPT.format(
        style_cn=style.style_cn,
        style_en=style.style_en,
        cards_block=cards_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    by_name = {c.name: c for c in targets}
    generated = 0
    for item in (data.get("sheets") or []):
        if not isinstance(item, dict):
            continue
        card = by_name.get(clip(item.get("name"), 200))
        if card is None:
            continue  # LLM 自创角色不收
        cn = clip(item.get("ref_prompt_cn"), 800)
        if not cn:
            continue
        card.ref_prompt_cn = cn
        card.ref_prompt_en = clip(item.get("ref_prompt_en"), 600)
        generated += 1
    if not generated:
        raise DramaAssetError("这轮没出结果,请重试(模型返回的角色名对不上)。")

    db.commit()
    return {
        "cards": [character_card_dict(c, style) for c in cards],
        "generated": generated,
    }
