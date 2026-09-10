# app/engines/adapt.py
# -*- coding: utf-8 -*-
"""改编共用件:把小说的结构化资产喂给下游再创作(漫剧 / 剧本)。

为什么单开这个模块:2026-09-10 审查发现两条改编链的口径是分叉的——
漫剧吃得到 本书基因(DNA)/ 创作偏好档案 / 作者雷区 / 章末契约的未决线索,
且源章正文保头尾去中段;而剧本改编只拿 `chapter.final_content[:600]` 的纯
头部截断,一本书 80% 的内容在改编时凭空消失,还丢了味道与雷区。

本模块是**叶子**:只依赖 db 模型 / schemas / 既有引擎函数,不 import drama 或
api 层。两条改编线都往这里看,口径只此一份,不再各自漂移。

边界:这里只管「把书的资产取出来、拼成提示词块」,不管怎么用这些块——
切集、写剧本、生成分镜各自的 prompt 组装仍归各线自己。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState, Project

# 改编素材的源章正文总预算(字符)。与漫剧线 drama/script.py 的 _MAX_CHAPTER_CHARS
# 对齐:够放下 3 章每章 3000 字的头尾精华,再多只会稀释注意力。
DEFAULT_SOURCE_BUDGET = 9000

# 单章保底字数:章数多时按章平分会被砍成碎片,低于这个值不如少喂几章
_MIN_PER_CHAPTER = 800


def head_tail(body: str, keep: int) -> str:
    """超预算的文本保头尾去中段(头 60% 尾 40%)。

    开头是衔接上文的关键,结尾是卡点与钩子的来源——纯头部截断会把章尾砍掉,
    改编出来的东西往往"收不住",根子就在这里。
    """
    if keep <= 0 or len(body) <= keep:
        return body
    head = keep * 6 // 10
    tail = keep - head
    return body[:head] + "\n……(中略)……\n" + body[-tail:]


def dna_block(project: Project) -> str:
    """本书基因(故事 DNA,作者的「定味锚」);空 DNA → 空串。"""
    from app.schemas.dna import coerce_dna

    dna = coerce_dna(project.dna)
    if dna.is_empty():
        return ""
    return "【本书基因(作者的定味锚,改编遵循)】\n" + dna.render() + "\n"


def profile_block(project: Project) -> str:
    """创作偏好档案(文风 / 禁忌避雷 / 读者定位);无档案 → 空串。"""
    from app.engines.media.text import clip
    from app.engines.tendency.assembler import _PROFILE_KEY, _PROFILE_LABELS

    profile = (project.global_tendency or {}).get(_PROFILE_KEY)
    if not isinstance(profile, dict):
        return ""
    lines = [
        f"  {label}:{clip(str(profile.get(key) or ''), 200)}"
        for key, label in _PROFILE_LABELS
        if str(profile.get(key) or "").strip()
    ]
    if not lines:
        return ""
    return "【创作偏好档案(作者的整书主张,改编遵循)】\n" + "\n".join(lines) + "\n"


def book_assets_block(project: Project) -> str:
    """书级资产块:DNA + 创作偏好档案。改编不该丢味,这是最小必带的定味信息。"""
    return dna_block(project) + profile_block(project)


# 改编时注入的关键事实条数上限。改编素材本身已有正文(token 大头),事实块只
# 补「正文里读不出来的此刻状态」——给太多反而稀释注意力(与检索层同一取向)。
_MAX_FACTS = 14
# 关键事实的内容截断:事实内容通常一句话,截断只为防抽取异常产生的超长条目
_FACT_CHARS = 120


def chapter_facts(
    db: Session, project_id: int, chapter_numbers: list[int], *, limit: int = _MAX_FACTS
) -> list[dict]:
    """这些章时刻**仍然有效**的关键事实(改编线的事实层,§5.1)。

    与小说线的检索层口径一致(都走 `BibleService.query_facts_at` 的时序过滤),
    但**不依赖场景卡**:改编是按章取材,没有"这一场"这个概念,若强行走
    `retrieve_for_scene` 就得先造一个假场景——那是本末倒置。

    取哪一章的时刻:改编吃的是「这几章合起来的剧情」,取并集的**最后一章**——
    那是最新的状态;若取最早的章,后续章里已经作废的事实(如"左臂还完好")
    会被当成硬约束喂进去,改编必错。

    排序 critical → major → minor,超限时先砍 minor(与检索层同序)。
    """
    nums = sorted({int(n) for n in (chapter_numbers or [])})
    if not nums:
        return []
    from app.engines.consistency import BibleService

    bible = BibleService(db, project_id)
    try:
        facts = bible.query_facts_at(nums[-1])
    except Exception:  # noqa: BLE001 — 事实层是增强,取不到不能拖垮改编
        return []
    retired = bible.retired_entity_ids()
    if retired:
        facts = [f for f in facts if f.entity_id not in retired]

    rank = {"critical": 0, "major": 1, "minor": 2}
    facts.sort(key=lambda f: rank.get(f.importance, 1))
    out: list[dict] = []
    for f in facts[:limit]:
        out.append({
            "fact_id": f.id,
            "content": (f.content or "")[:_FACT_CHARS],
            "entity_name": bible.entity_name(f.entity_id),
            "importance": f.importance or "major",
            "from_chapter": f.valid_from,
            "fact_type": f.fact_type or "",
        })
    return out


def facts_block(
    db: Session, project_id: int, chapter_numbers: list[int], *, limit: int = _MAX_FACTS
) -> str:
    """关键事实块(改编 prompt 用)。无事实 → 空串(整块省略)。

    **明确"保真"与"可再创作"的边界**:critical 必须一字不差地体现,major/minor
    允许按改编形态重组。这是 §5.1 待决项的落地口径——不区分的话,模型要么
    把细节当铁律(改编僵硬),要么把关键设定当细节(改编失真)。
    """
    facts = chapter_facts(db, project_id, chapter_numbers, limit=limit)
    if not facts:
        return ""
    lines: list[str] = []
    for f in facts:
        who = f"{f['entity_name']}:" if f.get("entity_name") else ""
        mark = "❗必须保真" if f["importance"] == "critical" else "·可再创作"
        since = f"(自第{f['from_chapter']}章起)" if f.get("from_chapter") else ""
        lines.append(f"  {mark} {who}{f['content']}{since}")
    return (
        "【改编时的既有事实(截至这几章的原始设定)】\n"
        "说明:❗标记的必须保真——改错读者会发现;其余允许按改编形态重组,\n"
        "但不得与之矛盾。这些是正文里读不出来的「此刻状态」,是改编的依据。\n"
        + "\n".join(lines) + "\n"
    )


def banned_block(db: Session, project_id: int) -> str:
    """作者雷区块(再创作口径)。

    与 consistency/motifs.banned_block 同源(都读 banned_rows),但措辞不同:
    那一份是给「继续写小说」用的,这一份针对「在已有正文之上再创作」——
    只约束新设计的部分,源正文里已有的内容按正文忠实改编,不受此限。
    """
    from app.engines.consistency.motifs import banned_rows

    rows = banned_rows(db, project_id)
    if not rows:
        return ""
    lines = [f"  - {r.label}" + (f":{r.detail}" if r.detail else "") for r in rows]
    return (
        "【作者雷区(再创作硬约束:新写的钩子/卡点/标题不得使用以下桥段或意象,"
        "换措辞也算;源正文里已有的内容不在此列,按正文忠实改编)】\n"
        + "\n".join(lines) + "\n"
    )


def open_threads(
    db: Session, project_id: int, chapter_numbers: list[int],
    *, per_chapter: int = 3, limit: int = 12,
) -> list[str]:
    """取若干章的章末未决线索(open_threads)。

    这是「下一章该接什么」的权威记录,比蓝图简述更适合当钩子与卡点的原料。
    无契约 / 契约过期(正文指纹不匹配)的章自动跳过。
    """
    from app.engines.pipeline.handoff import _fresh_contract

    out: list[str] = []
    chapters = {
        c.chapter_number: c
        for c in db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number.in_(chapter_numbers))
        .all()
    }
    for n in sorted(chapters):
        ch = chapters[n]
        state = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        contract = _fresh_contract(state, ch)
        if not contract:
            continue
        for t in (contract.get("open_threads") or [])[:per_chapter]:
            s = str(t or "").strip()
            if s and s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out[:limit]


def open_threads_block(
    db: Session, project_id: int, chapter_numbers: list[int],
    *, per_chapter: int = 3, limit: int = 12,
) -> str:
    threads = open_threads(
        db, project_id, chapter_numbers, per_chapter=per_chapter, limit=limit
    )
    if not threads:
        return ""
    return (
        "【章末未决线索(原书在此处欠着的悬念,改编应保留或兑现)】\n"
        + "\n".join(f"  - {t}" for t in threads) + "\n"
    )


def source_text(
    db: Session, project_id: int, chapter_numbers: list[int],
    budget: int = DEFAULT_SOURCE_BUDGET,
) -> tuple[str, list[int]]:
    """多章正文拼接(带章号小标题),总量控制在 budget 字符内。

    并集的每一章都要进素材——只喂主章会把并进来的章静默丢掉。
    预算按章平分(单章不低于 _MIN_PER_CHAPTER,避免章多时每章被砍成碎片),
    超预算的章保头尾去中段。
    返回 (拼接文本, 真的有正文的章号)。
    """
    if not chapter_numbers:
        return "", []
    per = max(_MIN_PER_CHAPTER, budget // len(chapter_numbers))
    texts: list[str] = []
    got: list[int] = []
    rows = {
        c.chapter_number: c
        for c in db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number.in_(chapter_numbers))
        .all()
    }
    for n in sorted(rows):
        row = rows[n]
        # 定稿优先,兜底草稿(与漫剧线 chapter_final_text 同口径:approved 前
        # 正文可能只在 draft_content)
        body = (row.final_content or "").strip() or (row.draft_content or "").strip()
        if not body:
            continue
        got.append(n)
        texts.append(f"—— 第 {n} 章 ——\n{head_tail(body, per)}")
    return "\n\n".join(texts)[:budget], got
