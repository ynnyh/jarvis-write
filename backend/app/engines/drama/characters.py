# app/engines/drama/characters.py
# -*- coding: utf-8 -*-
"""角色视觉卡 + 场景定调卡(资产层,借鉴 AnimaHub 素材库)。

角色卡从故事圣经 Entity 批量生成;locked 的卡重跑时跳过不覆盖
(AI-Novel-Writing-Assistant 的待确认/锁定机制)。场景卡从蓝图 scene_location
与圣经 location 实体归纳。一个 job 两次 LLM 调用。
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaSceneCard,
    Entity,
    Fact,
    Outline,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json, salvage_json_objects
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

# 每次 LLM 调用最多几条:每条 100-160 字中文 + 30-50 词英文,4 条约 1.2k 字。
# 曾经一次要 8 条 → 推理模型思考吃掉大半预算,JSON 停在半个字符串上,整批全丢。
# 宁可多调几次短的,也不赌一次长输出不被截断。
_REF_CHUNK = 4
# 名字对不上时,单条重问的上限(单条输出极短,几乎不可能再被截断)
_REF_RETRY_CAP = 4

# 名字归一:模型爱把 name 写成「【沈砚】」「沈砚(主角)」「沈 砚」
_WRAPPER = re.compile(r"^[【\[「『（(<《\"'\s]+|[】\]」』）)>》\"'\s]+$")
# 括号后缀(「沈砚(主角)」)。要求前面至少有一个字符,否则「(沈砚)」会被整段吃掉
_PAREN_SUFFIX = re.compile(r"(?<=.)[(（][^)）]*[)）]\s*$")
_FILLER = re.compile(r"[\s　·・.、,,:：\-—_]+")


def _norm_name(raw: object) -> str:
    """角色名归一,用于宽容匹配(只用来找卡,落库仍用卡上的原名)。"""
    s = str(raw or "").strip()
    s = _PAREN_SUFFIX.sub("", s).strip()
    s = _WRAPPER.sub("", s).strip()
    return _FILLER.sub("", s)


def _pick(item: dict, *keys: str) -> str:
    """按候选键顺序取第一个非空标量值(模型常把键名写成同义词)。"""
    for k in keys:
        v = item.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return ""


def _sheet_items(raw: str) -> list[dict]:
    """从模型输出里取出定妆照条目:正常 JSON → 顶层数组/截断抢救,逐层退。"""
    data = parse_llm_json(raw)
    items = data.get("sheets") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        items = salvage_json_objects(raw)
    return [i for i in items if isinstance(i, dict)]


def _assemble_ref_prompt(card: DramaCharacterCard, style) -> tuple[str, str]:
    """引擎确定性拼一条定妆照提示词(模型失手时的兜底,与分镜锚段兜底同思路)。

    定妆照提示词的成分本来就是死的——构图规范 + 这张卡的外貌锚 + 画风锚,
    不需要创作。所以模型漏条/名字对不上时引擎自己拼一条同样能用的,
    绝不让用户点了按钮只拿到一句「请重试」。
    """
    parts = [
        "单人正面半身居中,纯色浅灰背景,柔和均匀光,无动作无表情表演,画面里不出现任何文字",
        clip(card.appearance_cn, 400),
        f"标志服饰:{clip(card.outfit_cn, 120)}" if card.outfit_cn else "",
        clip(getattr(style, "style_cn", ""), 300),
    ]
    en_parts = [
        "character reference sheet, single person, front view, upper body, "
        "plain background, soft even lighting, no text",
        clip(card.appearance_en, 300),
        clip(getattr(style, "style_en", ""), 200),
    ]
    cn = ";".join(p for p in parts if p)
    en = ", ".join(p for p in en_parts if p)
    return cn[:800], en[:600]


def _cards_block(cards: list[DramaCharacterCard]) -> str:
    """本批角色的素材块。名字用 [] 包(与提示词里「照抄方括号里的名字」呼应),
    不用【】——中文书名号更容易被模型连着抄进 name 字段。"""
    return "\n".join(
        f"{i}. [{c.name}] 锁定外貌:{clip(c.appearance_cn, 400)}"
        + (f";英文:{clip(c.appearance_en, 300)}" if c.appearance_en else "")
        + (f";标志服饰:{clip(c.outfit_cn, 120)}" if c.outfit_cn else "")
        for i, c in enumerate(cards, 1)
    )


def _apply_sheet(card: DramaCharacterCard, item: dict) -> bool:
    """把一条结果写进卡;中文轨为空视为没出(英文轨单独存在也没法当主提示词用)。"""
    cn = clip(_pick(item, "ref_prompt_cn", "prompt_cn", "ref_prompt", "prompt", "cn", "中文"), 800)
    if not cn:
        return False
    card.ref_prompt_cn = cn
    card.ref_prompt_en = clip(
        _pick(item, "ref_prompt_en", "prompt_en", "en", "英文"), 600
    )
    return True


def _match_sheets(
    items: list[dict], batch: list[DramaCharacterCard]
) -> tuple[set[int], list[str]]:
    """把模型返回的条目落到卡上,返回(已写入的卡 id 集合, 模型报出的名字列表)。

    匹配顺序:原名逐字 → 归一后 → 唯一包含 → (只有一条且只要一张卡时)按位置。
    位置匹配只在「一条对一卡」时才用:多条时若按位置乱配,会把甲的脸描述
    写到乙的卡上——那种错用户看不出来,却会毁掉整片的人物一致性,
    宁可让它落空、由引擎兜底拼一条正确的。
    """
    by_exact = {c.name: c for c in batch}
    by_norm: dict[str, DramaCharacterCard] = {}
    for c in batch:
        key = _norm_name(c.name)
        if key:
            by_norm.setdefault(key, c)

    done: set[int] = set()
    reported: list[str] = []
    for item in items:
        raw_name = _pick(item, "name", "character", "role", "角色", "角色名", "姓名")
        if raw_name:
            reported.append(raw_name)
        norm = _norm_name(raw_name)
        card = by_exact.get(raw_name) or (by_norm.get(norm) if norm else None)
        if card is None and norm:
            hits = [c for k, c in by_norm.items() if k in norm or norm in k]
            if len(hits) == 1:
                card = hits[0]
        if card is None and len(items) == 1 and len(batch) == 1:
            card = batch[0]  # 一条对一卡:名字写跑了也不会配错人
        if card is None or card.id in done:
            continue
        if _apply_sheet(card, item):
            done.add(card.id)
    return done, reported


async def _ask_sheets(adapter, style, batch: list[DramaCharacterCard]) -> list[dict]:
    prompt = REF_SHEET_PROMPT.format(
        style_cn=style.style_cn,
        style_en=style.style_en,
        count=len(batch),
        cards_block=_cards_block(batch),
    )
    return _sheet_items(await adapter.ask(prompt))


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

    可靠性(这个按钮以前会「经常失败」,根因是模型输出被截断):
    每 4 张一批分开调用 → 条目宽容抽取(键名同义词/顶层数组/半截 JSON 抢救)
    → 名字宽容匹配 → 仍缺的单条重问 → 最后由引擎确定性拼装兜底。
    返回 cards / generated(模型出的条数)/ assembled(引擎兜底拼的条数)。
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
            "assembled": 0,
        }
    targets = targets[:_MAX_REF_SHEETS]

    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    total = len(targets)
    done: set[int] = set()
    reported: list[str] = []
    for start in range(0, total, _REF_CHUNK):
        batch = targets[start : start + _REF_CHUNK]
        progress(
            f"AI 正在写定妆照提示词({start + 1}-{start + len(batch)}/{total} 位)…"
        )
        items = await _ask_sheets(adapter, style, batch)
        got, said = _match_sheets(items, batch)
        done |= got
        reported.extend(said)

    # 纠偏:仍缺的单条重问一次(单条输出极短,且「一条对一卡」时名字写跑也能落位)
    missing = [c for c in targets if c.id not in done]
    for card in missing[:_REF_RETRY_CAP]:
        progress(f"「{card.name}」这条没对上,正在单独重问…")
        items = await _ask_sheets(adapter, style, [card])
        got, said = _match_sheets(items, [card])
        done |= got
        reported.extend(said)

    # 兜底:模型三番四次没给的,引擎自己拼——成分是死的,不需要创作
    assembled = 0
    for card in targets:
        if card.id in done or not (card.appearance_cn or card.appearance_en):
            continue  # 连外貌锚都没有的卡拼不出东西,让它继续缺,由报错点名
        cn, en = _assemble_ref_prompt(card, style)
        card.ref_prompt_cn = cn
        card.ref_prompt_en = en
        assembled += 1
        done.add(card.id)

    if not done:
        raise DramaAssetError(_ref_failure_hint(targets, reported))

    db.commit()
    return {
        "cards": [character_card_dict(c, style) for c in cards],
        "generated": len(done) - assembled,
        "assembled": assembled,
    }


def _ref_failure_hint(targets: list[DramaCharacterCard], reported: list[str]) -> str:
    """一条也没成时的报错:说清到底是哪一环坏了,不再一律甩「角色名对不上」。

    以前无论截断、空返回、名字不符都报同一句「模型返回的角色名对不上」,
    用户照着排查名字只会白费功夫——真正的原因多半是输出被截断。
    """
    who = "、".join(c.name for c in targets[:5])
    lack = [c.name for c in targets if not (c.appearance_cn or c.appearance_en)]
    if lack:
        return (
            f"这些角色卡还没有「锁定外貌」段,写不出定妆照:{'、'.join(lack[:5])}。"
            "先点「生成资产卡」补齐外貌,再出定妆照。"
        )
    if not reported:
        return (
            f"模型这次没吐出可用的结果(要 {len(targets)} 位:{who}),"
            "多半是输出被截断或渠道空转。已自动放大预算重试过,仍失败请重试一次;"
            "反复如此就到「模型设置」把该配置的 max_tokens 调大,或换一个思考更省的模型。"
        )
    return (
        f"模型返回的角色名对不上:它给的是「{'、'.join(reported[:5])}」,"
        f"本书要的是「{who}」。请重试(或换个模型)。"
    )
