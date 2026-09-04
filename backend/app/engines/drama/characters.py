# app/engines/drama/characters.py
# -*- coding: utf-8 -*-
"""角色视觉卡 + 场景定调卡(资产层,借鉴 AnimaHub 素材库)。

角色卡从故事圣经 Entity 批量生成;locked 的卡重跑时跳过不覆盖
(AI-Novel-Writing-Assistant 的待确认/锁定机制)。场景卡从蓝图 scene_location
与圣经 location 实体归纳。一个 job 两次 LLM 调用。
"""
from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaSceneCard,
    Entity,
    Fact,
    Outline,
    Project,
    Relationship,
)
from app.engines.consistency.extractor import parse_llm_json, salvage_json_objects
from app.engines.drama.common import (
    character_card_dict,
    clip,
    concept_block,
    scene_card_dict,
    style_card,
)
from app.engines.drama.gender import (
    gender_directive,
    gender_phrase_cn,
    gender_phrase_en,
    gender_tag,
    infer_gender,
    normalize_gender,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import CHARACTER_PROMPT, REF_SHEET_PROMPT, SCENE_PROMPT

# 单次批量上限:防提示词与输出失控(超出的角色按事实条数排序取前 N,并在
# 返回值里明示总数——此前是静默截断,圣经里第 13 个之后的角色无任何入口)
_MAX_CHARACTERS = 12
_MAX_SCENES = 10
_MAX_REF_SHEETS = 8  # 定妆照提示词单次批量上限(每条比视觉卡更长)
# 判性别时扫多少条事实:比 digest 宽得多——digest 有限长,
# 性别线索(「她」「夫人」)经常正好被截掉,于是模型只能靠猜,一猜就把女角写成男的
_GENDER_FACT_LIMIT = 40
# 角色档案摘要的素材窗口:外貌线索散落在事实与关系里,300 字窗口经常把
# 「月白襦裙」「左脸刀疤」截掉,视觉卡只能瞎补——放宽为结构化三段、600 字封顶
_DIGEST_FACT_LIMIT = 8
_DIGEST_REL_LIMIT = 2
_DIGEST_CHARS = 600


class DramaAssetError(ValueError):
    """资产生成的业务性错误(信息直接上屏,如「圣经里还没有角色」)。"""


def _entity_digest(db: Session, entity: Entity) -> str:
    """角色档案摘要:base_profile 平铺 + 最近事实 + 现行关系边。

    结构化三段(profile/事实/关系)而非纯平铺——外貌、服饰、与他人关系是
    视觉卡与声线的直接依据,聚合时各自保住窗口,不再被 300 字一刀切截掉。
    """
    profile = entity.base_profile if isinstance(entity.base_profile, dict) else {}
    parts = [f"{k}:{v}" for k, v in profile.items() if str(v or "").strip()]
    facts = (
        db.query(Fact.content)
        .filter(Fact.entity_id == entity.id)
        .order_by(Fact.valid_from.desc(), Fact.id.desc())
        .limit(_DIGEST_FACT_LIMIT)
        .all()
    )
    parts.extend(str(c) for (c,) in facts if c)
    parts.extend(_current_relations(db, entity))
    digest = ";".join(p for p in parts if p)
    return clip(digest, _DIGEST_CHARS)


def _current_relations(db: Session, entity: Entity) -> list[str]:
    """该角色现行的关系边(未失效的最近 N 条),渲染成「与某人:关系」。

    关系是角色辨识度与对手戏设计的重要线索(「反目成仇的兄长」直接影响
    外貌沧桑感与声线),此前完全不进 digest。
    """
    rels = (
        db.query(Relationship)
        .filter(
            (
                (Relationship.from_entity_id == entity.id)
                | (Relationship.to_entity_id == entity.id)
            ),
            Relationship.valid_until.is_(None),
        )
        .order_by(Relationship.id.desc())
        .limit(_DIGEST_REL_LIMIT)
        .all()
    )
    if not rels:
        return []
    other_ids = [
        r.to_entity_id if r.from_entity_id == entity.id else r.from_entity_id
        for r in rels
    ]
    names = {
        e.id: e.name
        for e in db.query(Entity).filter(Entity.id.in_(other_ids)).all()
    }
    out: list[str] = []
    for r in rels:
        other = names.get(
            r.to_entity_id if r.from_entity_id == entity.id else r.from_entity_id
        )
        if other and str(r.relation or "").strip():
            out.append(f"与{other}:{str(r.relation).strip()}")
    return out


def _gender_evidence(db: Session, entity: Entity) -> list[str]:
    """判性别的证据面:别名 + 档案全文 + 最近 40 条事实,都不截断。

    刻意跟 digest 分开扫:digest 要控长(300 字)会把「她」「夫人」这类线索截掉,
    而性别一旦缺失,模型就按提示词里的示例惯性写——女角色于是出成了男相。
    """
    parts = [entity.name, "/".join(str(a) for a in (entity.aliases or []))]
    profile = entity.base_profile if isinstance(entity.base_profile, dict) else {}
    parts.extend(str(v) for v in profile.values() if str(v or "").strip())
    facts = (
        db.query(Fact.content)
        .filter(Fact.entity_id == entity.id)
        .order_by(Fact.valid_from.desc(), Fact.id.desc())
        .limit(_GENDER_FACT_LIMIT)
        .all()
    )
    parts.extend(str(c) for (c,) in facts if c)
    return [p for p in parts if str(p or "").strip()]


def _resolve_gender(
    db: Session, entity: Entity | None, card: DramaCharacterCard | None
) -> tuple[str, str]:
    """这个角色本轮下发给模型的性别,返回 (gender, 依据人话)。

    优先级:卡上已有 > 档案推断 > 未定(交模型判断、由用户在卡上拍板)。
    卡上已有的最硬——那要么是用户手动改过的,要么是上一轮按同一份档案推出来的,
    重跑不该把用户拍板的性别再改回去。
    """
    pinned = normalize_gender(getattr(card, "gender", ""))
    if pinned:
        return pinned, "卡上已定"
    if entity is None:
        return "", ""
    return infer_gender(*_gender_evidence(db, entity))


async def generate_character_cards(
    db: Session, project: Project, progress=lambda s: None
) -> dict:
    """从故事圣经批量生成/更新角色卡(locked 跳过),返回 {cards, skipped_locked, ...}。

    超过 _MAX_CHARACTERS 时**不再静默截断**:按事实条数(≈出场戏份)排序取前 N,
    返回值带 characters_total/characters_shown,前端据此提示「还有 N 个角色未生成」。
    """
    entities = (
        db.query(Entity)
        .filter(
            Entity.project_id == project.id,
            Entity.entity_type == "character",
            Entity.retired.is_(False),
        )
        .all()
    )
    if not entities:
        raise DramaAssetError(
            "故事圣经里还没有角色——先在写作区定稿几章让引擎抽取角色,再来生成角色卡。"
        )
    # 按事实条数(≈戏份)降序排:有限的名额给主要角色,配角不会挤掉主角
    fact_counts = dict(
        db.query(Fact.entity_id, func.count(Fact.id))
        .filter(Fact.entity_id.in_([e.id for e in entities]))
        .group_by(Fact.entity_id)
        .all()
    )
    entities.sort(key=lambda e: (-int(fact_counts.get(e.id, 0)), e.id))
    characters_total = len(entities)
    entities = entities[:_MAX_CHARACTERS]

    # 既有卡索引:entity_id 优先,名字兜底。要在拼提示词之前建好——
    # 卡上已拍板的性别得当硬约束下发,否则重跑一次又被模型改回去
    existing = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .all()
    )
    by_entity = {c.entity_id: c for c in existing if c.entity_id}
    by_name = {c.name: c for c in existing}

    lines = []
    genders: dict[str, str] = {}  # 角色名 → 本轮下发的性别(空 = 让模型自己判断)
    for e in entities:
        aliases = "/".join(str(a) for a in (e.aliases or []))
        head = f"{e.name}" + (f"(又称:{aliases})" if aliases else "")
        gender, why = _resolve_gender(db, e, by_entity.get(e.id) or by_name.get(e.name))
        genders[e.name] = gender
        lines.append(f"【{head}】{gender_directive(gender, why)}|{_entity_digest(db, e)}")
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
        # 性别:档案/卡上定了的说一不二,没定才用模型自己判断的结果
        target.gender = genders.get(name) or normalize_gender(item.get("gender"))
        target.appearance_cn = clip(item.get("appearance_cn"), 600)
        target.appearance_en = clip(item.get("appearance_en"), 400)
        target.outfit_cn = clip(item.get("outfit_cn"), 120)
        target.voice_desc = clip(item.get("voice_desc"), 120)
        cards_out.append(character_card_dict(target, style))

    db.commit()
    return {
        "cards": cards_out,
        "skipped_locked": skipped_locked,
        "characters_total": characters_total,
        "characters_shown": len(entities),
    }


def _one_card_item(raw: str) -> dict | None:
    """单卡重出:从模型输出里取出那唯一一条(cards 数组 → 顶层对象 → 截断抢救)。

    只重一张卡,所以不校名字——「一条对一卡」时名字写跑了也配不错人
    (同 _match_sheets 的那条道理)。
    """
    data = parse_llm_json(raw)
    if isinstance(data, dict) and (data.get("appearance_cn") or data.get("appearance_en")):
        return data  # 模型直接给了一张卡,没套 {"cards": [...]}
    items = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        items = salvage_json_objects(raw)
    for it in items:
        if isinstance(it, dict) and (it.get("appearance_cn") or it.get("appearance_en")):
            return it
    return None


async def regenerate_character_card(
    db: Session, project: Project, card_id: int, progress=lambda s: None
) -> dict:
    """只重出这一张角色卡(显式重出 = 覆盖,连 locked 的也覆盖),返回 {card}。

    为什么要有单卡入口:批量「重新生成」会动所有卡,而用户往往只想修一张——
    最典型的就是「这个女角色被写成男的了」。这里把卡上拍板的性别当成
    不可违抗的硬约束下发,重写外貌/服饰/声线三段;定妆照提示词不动
    (它有自己的「重出提示词」,重写完外貌再点一次即可)。
    """
    card = db.get(DramaCharacterCard, card_id)
    if card is None or card.project_id != project.id:
        raise DramaAssetError("角色卡不存在。")

    entity = db.get(Entity, card.entity_id) if card.entity_id else None
    gender, why = _resolve_gender(db, entity, card)
    digest = _entity_digest(db, entity) if entity is not None else ""
    if not digest:
        # 圣经里没有这个角色(用户手加的卡):拿卡上现有描述当档案,别让模型凭空捏
        digest = clip(
            ";".join(
                p for p in (card.appearance_cn, card.outfit_cn, card.voice_desc) if p
            ),
            300,
        ) or "(圣经里没有这个角色的档案,按角色名与本书类型合理设计)"
    aliases = "/".join(str(a) for a in ((entity.aliases if entity else None) or []))
    head = card.name + (f"(又称:{aliases})" if aliases else "")

    progress(f"AI 正在重写「{card.name}」的角色卡…")
    adapter = get_adapter_for(Task.DRAMA_ASSET, timeout=300)
    prompt = CHARACTER_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        characters_block=f"【{head}】{gender_directive(gender, why)}|{digest}",
    )
    item = _one_card_item(await adapter.ask(prompt))
    if item is None:
        raise DramaAssetError(
            f"模型这次没吐出可用的结果(要重写「{card.name}」),多半是输出被截断。"
            "直接重试一次;反复如此就到「模型设置」把该配置的 max_tokens 调大,或换个模型。"
        )

    card.gender = gender or normalize_gender(item.get("gender")) or (card.gender or "")
    # 逐字段兜底:模型漏给哪段就保留原值,不把已有的描述清空
    card.appearance_cn = clip(item.get("appearance_cn"), 600) or card.appearance_cn
    card.appearance_en = clip(item.get("appearance_en"), 400) or card.appearance_en
    card.outfit_cn = clip(item.get("outfit_cn"), 120) or card.outfit_cn
    card.voice_desc = clip(item.get("voice_desc"), 120) or card.voice_desc
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project.id))}


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
    return {
        "cards": chars["cards"],
        "skipped_locked": chars["skipped_locked"],
        "characters_total": chars.get("characters_total"),
        "characters_shown": chars.get("characters_shown"),
        **scenes,
    }


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
        gender_phrase_cn(card.gender),
        clip(card.appearance_cn, 400),
        f"标志服饰:{clip(card.outfit_cn, 120)}" if card.outfit_cn else "",
        clip(getattr(style, "style_cn", ""), 300),
    ]
    en_parts = [
        "character reference sheet, single person, front view, upper body, "
        "plain background, soft even lighting, no text",
        gender_phrase_en(card.gender),
        clip(card.appearance_en, 300),
        clip(getattr(style, "style_en", ""), 200),
    ]
    cn = ";".join(p for p in parts if p)
    en = ", ".join(p for p in en_parts if p)
    return cn[:800], en[:600]


def _cards_block(cards: list[DramaCharacterCard]) -> str:
    """本批角色的素材块。名字用 [] 包(与提示词里「照抄方括号里的名字」呼应),
    不用【】——中文书名号更容易被模型连着抄进 name 字段。
    性别单列一项:定妆照是「这张脸」的第一次落地,性别在这里写错,
    后面每一格都会跟着错。"""
    return "\n".join(
        f"{i}. [{c.name}] "
        + (f"{gender_tag(c.gender)};" if gender_tag(c.gender) else "")
        + f"锁定外貌:{clip(c.appearance_cn, 400)}"
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
