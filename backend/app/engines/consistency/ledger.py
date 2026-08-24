# app/engines/consistency/ledger.py
# -*- coding: utf-8 -*-
"""角色资源账本:谁手上有什么、会什么(possession / ability 事实的专用视图)。

这些行本来就在 Fact 表里,此前只以「人物当前状态」的身份混在 hard_constraints_block
里一起注入。混着有三个治不了的毛病:

1. **没有闭集红线**。角色手上有什么全凭模型记性——第 12 章凭空掏出一把从没提过的匕首、
   把第 3 章送出去的玉佩又戴回来,门禁也看不出(checker 拿到的是同一份混排清单,
   分不清哪条是「资源」)。角色那边早有 known_roster_block 的闭集约束,资源这边一直没有。
2. **抢预算**。hard_constraints 只按 importance 排序后截到 _MAX_FACT_LINES,长篇后期
   状态事实一多,minor 的「持有」行最先被挤掉——恰恰资源是最需要一直记着的东西。
3. **不收口**。用掉/送出/损毁没人要求写明,于是 replaces 给不出来,开区间越攒越长,
   账本越写越假(干粮吃完了三章,清单里还挂着「持有半块干粮」)。

所以这里给资源一份**自己的**渲染和预算,并从 hard_constraints 里分流出去(不重复注入)。
不新增表、不新增存储:全是对既有 Fact 行的现查派生,与 devices.py 同一路子。

与常驻装置(devices.py)的分工:装置是**宪法里预先登记**的金手指/信物,由宪法块全程
声明、断档了还要催场;账本管的是**故事里挣来的**东西(捡的、赢的、学会的、欠下的),
只在「别凭空掏出、别忘了收口」这件事上出手,不催场——没有 per-chapter 的资源出场契约
可以算断档,硬猜等于乱催。
"""
from __future__ import annotations

import logging

from app.db.models import Fact
from app.engines.consistency.bible import RESOURCE_FACT_TYPES, BibleService

logger = logging.getLogger("jarvis-write.ledger")

# 账本块最多注入的行数:比状态事实的上限小一档——资源条目单条更短,且真正值得
# 长期记住的关键资源本就不该有几十条。超限同样按 importance 砍,critical 永不被砍。
_MAX_RESOURCE_LINES = 24

_RANK = {"critical": 0, "major": 1, "minor": 2}


def resource_facts(
    bible: BibleService,
    chapter_number: int,
    entity_names: list[str] | None = None,
) -> list[Fact]:
    """第 chapter_number 章时刻仍然有效的资源事实(possession / ability)。

    已退场实体的资源不注入(与 hard_constraints 同一口径:人退场了,他手上有什么
    不再约束后续生成)。超过 _MAX_RESOURCE_LINES 时按重要度截断并留日志,不静默丢。
    """
    facts = [
        f
        for f in bible.query_facts_at(chapter_number, entity_names)
        if f.fact_type in RESOURCE_FACT_TYPES
    ]
    retired = bible.retired_entity_ids()
    if retired:
        facts = [f for f in facts if f.entity_id not in retired]
    facts.sort(key=lambda f: _RANK.get(f.importance, 1))
    if len(facts) > _MAX_RESOURCE_LINES:
        logger.info(
            "第%d章资源账本 %d 条超上限 %d,按重要度截断(critical 优先保留)",
            chapter_number, len(facts), _MAX_RESOURCE_LINES,
        )
        facts = facts[:_MAX_RESOURCE_LINES]
    # 展示时按实体聚合:同一个人的东西排在一起,读着才像一份账本
    facts.sort(key=lambda f: (f.entity_id, _RANK.get(f.importance, 1)))
    return facts


def ledger_block(
    bible: BibleService,
    chapter_number: int,
    entity_names: list[str] | None = None,
) -> str:
    """草稿/定稿/门禁共用的资源账本块;账本为空 → 空串(零 token、零行为变化)。

    空账本刻意不注入红线:开篇几章人物本来就在不停地拿到新东西,此时「不许凭空掏出」
    只会压制正常叙事。红线要咬得住,前提是账本里真有东西可对照——
    真正的事故是「用掉的又出现」「明明有的却忘了」,两者都要求先有存量。
    """
    facts = resource_facts(bible, chapter_number, entity_names)
    if not facts:
        return ""
    lines = []
    for f in facts:
        mark = "❗" if f.importance == "critical" else "·"
        kind = "会/能" if f.fact_type == "ability" else "持有"
        lines.append(
            f"{mark} {bible.entity_name(f.entity_id)} {kind}:{f.content}"
            f"(自第{f.valid_from}章起)"
        )
    return (
        "【角色资源账本(闭集约束·硬规则)】\n"
        + "\n".join(lines)
        + "\n以上是账本里登记在册、本章仍然有效的关键道具与能力(只记会影响后续的,"
        "随手的杂物不入账)。三条硬规则:\n"
        "1. 不许凭空掏出:账本里没有的**关键**道具或本事,不能写成「他早就有」「随身一直带着」"
        "「素来会这个」直接拿出来用或化解危机。本章蓝图【关键道具】点名的不算凭空。\n"
        "2. 新增必须当场交代来源:本章确实要让人物得到新东西、学会新本事,就在正文里写清"
        "怎么来的(谁给的/哪儿捡的/花钱买的/拿什么换的/怎么练成的),别让它凭空出现在手里。\n"
        "3. 用掉、送出、损毁、失效、被夺走,都要在正文里明确写出来——写明了账本才收得住;"
        "已经失去的东西,后文不许再拿出来用。也不要让上面登记在册、本章明显该派上用场的"
        "关键道具/本事被人物彻底忘掉。"
    )
