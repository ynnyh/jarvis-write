# app/engines/consistency/reader_knowledge.py
# -*- coding: utf-8 -*-
"""读者认知集:把「读者此刻相信什么 / 还不知道什么」摆到台面上(§1.4)。

问题(docs/15 §1.4):反转写不好,通常不是文笔问题,而是**模型不知道要掀翻什么**。
模型知道真相(硬约束里有)、也知道角色知道什么(圣经有 KnowledgeState),但
**没有人告诉它「读者现在信的是哪一版」**。于是转折章写出来就是「角色突然说破真相」
——因为模型手里没有那张「读者此刻的认知地图」,它只能靠一句解释把真相塞过去。
真正好看的反转是:读者一直在按 A 理解某件事,某一天发现 A 是假的,回头每处
细节都变味。

这里做三件事,全部确定性(零 LLM):

  ① **读者认知清单**:从 KnowledgeState 现查派生——`knower="reader"` 且
     `known_from_chapter <= N` 的,是读者**已经知道**的;而还没披露的事实
     (在 Fact 表里、但读者没有对应 KnowledgeState)是**压着的底牌**。
  ② **反转预备(转折章专属)**:定位本章要掀翻什么,渲染成「读者当前相信
     → 真相是」的对照块,注入草稿 prompt。没找到可掀翻的东西就不注入。
  ③ **披露节奏(全书)**:算每章的读者新增披露量,发现「连续多章零披露」
     (悬念一直压着不放 → 读者疲劳)与「一章爆出一大堆」(填鸭 → 消化不良)。

为什么先做成 advisory(提示不阻断):
  「读者知道了吗」是主观判断——LLM 抽取的知识条目本身就有噪声,靠它硬卡门禁
  会误杀正常叙事(比如读者能推断出的东西不该算「已披露」)。所以这一层只
  **喂证据、不设卡口**,等抽取质量被验证后再考虑硬化(与 docs/15 的
  「先 advisory,看数据质量再决定是否硬化」一致)。

与既有模块的分工:
  - bible.py 的 KnowledgeState 管「谁知道」这个**数据**;
  - foreshadow.py / foreshadow_agenda.py 管「什么时候该说」这个**日程**;
  - 这里管「截至本章,读者手上那张认知地图长什么样」这个**视图**——
    反转设计需要的是这张图,不是原始数据行。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Entity, Fact, KnowledgeState, Outline

logger = logging.getLogger("jarvis-write.reader_knowledge")

# 反转预备最多注入几条对照:一次掀翻太多,读者只会晕;一章一个反转是上限。
_MAX_TWISTS = 2
# 压着的底牌(读者不知情的关键事实)最多列几条——它是「可用素材」,不是清单。
_MAX_HELD_CARDS = 6
# 只把 critical/major 的未披露事实当底牌:minor 的鸡毛蒜皮爆料不构成反转。
_HELD_IMPORTANCE = ("critical", "major")

_RANK = {"critical": 0, "major": 1, "minor": 2}

# 判定「本章是转折章」的阈值:认知颠覆标记达到这里才做反转预备。
# 蓝图里认知颠覆常写成 ★★★★☆(1-5 星)或「强/高」字样,两套都要认。
_TWIST_STAR_MIN = 3
_TWIST_STRONG_WORDS = ("强", "高")
# 定位/概要里出现这些词,也算转折信号(老蓝图没填认知颠覆字段时的兜底)
_TWIST_ROLE_WORDS = ("反转", "揭露", "真相", "颠覆", "转折", "揭晓", "身份败露")


@dataclass
class ReaderView:
    """截至第 N 章,读者手上那张认知地图。"""

    chapter_number: int = 0
    # 读者已知的(disclosed):(fact, 披露章)
    known: list[tuple[Fact, int]] = field(default_factory=list)
    # 读者还不知情的关键事实(held):压着的底牌
    held: list[Fact] = field(default_factory=list)
    # 角色层面的不对称:同一件事,角色已知但读者不知道(信息差道具)
    asymmetries: list[tuple[Fact, str, int]] = field(default_factory=list)
    # 本章是不是转折章 + 命中的信号词
    is_twist: bool = False
    twist_signals: list[str] = field(default_factory=list)

    @property
    def disclosure_count(self) -> int:
        return len(self.known)


def _twist_signals(outline: Outline | None) -> list[str]:
    """本章的转折信号词(空 list = 不是转折章)。

    三处来源,任一命中即算:
      1. plot_twist_level 的星级/强度字样(蓝图主字段);
      2. chapter_role 里的「反转/揭露/真相」类词;
      3. summary 里的同类词(老蓝图字段没填全时的兜底)。
    """
    if outline is None:
        return []
    signals: list[str] = []

    raw_twist = str(outline.plot_twist_level or "").strip()
    if raw_twist:
        stars = raw_twist.count("★")
        if stars >= _TWIST_STAR_MIN:
            signals.append(f"认知颠覆{raw_twist}")
        elif any(w in raw_twist for w in _TWIST_STRONG_WORDS) and "低" not in raw_twist:
            signals.append(f"认知颠覆{raw_twist}")

    role = str(outline.chapter_role or "")
    hit = [w for w in _TWIST_ROLE_WORDS if w in role]
    if hit:
        signals.append(f"章定位「{role}」")

    # 概要里的转折词只在没有其他信号时兜底——概要常出现「却」「原来」等普通叙述词,
    # 单独命中容易误报,所以星级/定位已经给信号时不再叠加。
    if not signals:
        summary = str(outline.summary or "")
        hit = [w for w in _TWIST_ROLE_WORDS if w in summary]
        if hit:
            signals.append(f"本章概要含「{hit[0]}」")
    return signals


def is_twist_chapter(outline: Outline | None) -> bool:
    """本章是不是「要掀翻读者既有判断」的转折章(纯规则,零 LLM)。"""
    return bool(_twist_signals(outline))


def build_reader_view(
    db: Session,
    project_id: int,
    chapter_number: int,
    *,
    outline: Outline | None = None,
) -> ReaderView:
    """算出截至第 N 章的读者认知地图。

    已披露 = KnowledgeState(knower="reader", known_from_chapter <= N)。
    还压着 = 第 N 章时刻有效、且读者没有披露记录的关键事实。
    不对称 = 有角色披露记录但读者没有(角色比读者先知道 → 可以被用成信息差)。
    """
    view = ReaderView(chapter_number=chapter_number)
    view.twist_signals = _twist_signals(outline)
    view.is_twist = bool(view.twist_signals)

    # 读者已知:一次查全部 reader 披露记录(项目内条数有界),按事实取最新披露章
    ks_rows = (
        db.query(KnowledgeState)
        .filter(
            KnowledgeState.project_id == project_id,
            KnowledgeState.knower == "reader",
            KnowledgeState.known_from_chapter <= chapter_number,
        )
        .all()
    )
    reader_disclosed: dict[int, int] = {}
    for ks in ks_rows:
        prev = reader_disclosed.get(ks.fact_id)
        if prev is None or int(ks.known_from_chapter) < prev:
            reader_disclosed[ks.fact_id] = int(ks.known_from_chapter)

    # 第 N 章时刻仍然有效的事实(与硬约束同一口径:退场实体的不参与)
    facts = (
        db.query(Fact)
        .filter(
            Fact.project_id == project_id,
            Fact.valid_from <= chapter_number,
        )
        .filter((Fact.valid_until.is_(None)) | (Fact.valid_until >= chapter_number))
        .all()
    )
    retired = {
        row.id
        for row in db.query(Entity.id).filter(
            Entity.project_id == project_id, Entity.retired.is_(True)
        )
    }
    if retired:
        facts = [f for f in facts if f.entity_id not in retired]

    held: list[Fact] = []
    for f in facts:
        disclosed_at = reader_disclosed.get(f.id)
        if disclosed_at is not None:
            # 读者已经知道这条(截至本章)。注意这里收的是「已披露的全部」而不是
            # 「本章刚披露的」——认知地图问的是「读者现在手上有什么」,不是「这章给了什么」。
            # (踩过一次:写成 `disclosed_at == chapter_number` 后,known 永远是空的,
            #  因为读者在本章之前学到的东西才是他此刻相信的基础。)
            view.known.append((f, disclosed_at))
            continue
        if f.importance in _HELD_IMPORTANCE:
            # 还没披露的关键事实。注意:新事实(valid_from==N,本章才产生)天然
            # 不是「压着的底牌」——它还没发生过,谈不上瞒着读者。
            if int(f.valid_from) < chapter_number:
                held.append(f)

    view.held = sorted(held, key=lambda f: _RANK.get(f.importance, 1))[:_MAX_HELD_CARDS]
    view.known.sort(key=lambda pair: (-pair[1], _RANK.get(pair[0].importance, 1)))

    # 不对称:角色已披露、读者未披露的同一事实(角色知道、读者被瞒着)
    ent_ks = (
        db.query(KnowledgeState)
        .filter(
            KnowledgeState.project_id == project_id,
            KnowledgeState.knower != "reader",
            KnowledgeState.known_from_chapter <= chapter_number,
        )
        .all()
    )
    fact_index = {f.id: f for f in facts}
    # 名字索引 = 出现在事实里的实体 + knower 角色。后者单独并入是因为:一个只
    # 「知道」却没有任何 Fact 挂在他名下的角色,不在 fact 实体的集合里,
    # 只查事实实体会把信息差里的人显示成「角色2」而不是名字。
    names = _entity_names(
        db, {f.entity_id for f in facts} | _knower_ids(ent_ks)
    )
    seen: set[tuple[int, str]] = set()
    for ks in ent_ks:
        if ks.fact_id in reader_disclosed:
            continue  # 读者也知道了,不构成不对称
        f = fact_index.get(ks.fact_id)
        if f is None:
            continue
        key = (ks.fact_id, ks.knower)
        if key in seen:
            continue
        seen.add(key)
        view.asymmetries.append((f, _knower_label(ks.knower, names), int(ks.known_from_chapter)))
    view.asymmetries.sort(key=lambda t: (-t[2], _RANK.get(t[0].importance, 1)))
    return view


def _entity_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        row.id: row.name
        for row in db.query(Entity.id, Entity.name).filter(Entity.id.in_(ids))
    }


def _knower_ids(rows: list[KnowledgeState]) -> set[int]:
    """从 KnowledgeState.knower(存储形式是 "reader" 或 entity_id 的字符串)取出角色 id。

    坑:knower 不一定是「有事实的角色」——一个只「知道」却没有任何 Fact 挂在他名下的
    角色,不会出现在 facts 的 entity_id 集合里。此前只拿事实实体的名字建索引,
    结果信息差里写的是「角色2」而不是「沈砚」。所以这里单独收集一次。
    """
    ids: set[int] = set()
    for ks in rows:
        try:
            ids.add(int(ks.knower))
        except (TypeError, ValueError):  # knower 非数字:该行跳过
            continue
    return ids


def _knower_label(knower: str, names: dict[int, str]) -> str:
    """knower 是 "reader" 或角色 entity_id 的字符串。"""
    try:
        return names.get(int(knower), f"角色{knower}")
    except (TypeError, ValueError):
        return str(knower)


# 注入正文 prompt 的反转预备块。注意口吻:这是「素材 + 要求」,不是命令式的
# 情节指令——具体怎么掀翻由模型定,但「掀翻什么」必须交代清楚,否则模型只能
# 让角色开口解释。
_TWIST_BLOCK = """\
【反转预备:读者此刻的认知地图(重要)】
{signals}
{believed_block}
{held_block}
{asymmetry_block}
写反转的第一原则:**不要靠人物开口解释真相**。「其实当年是他害了你」这类说破
是最廉价的写法。要让读者自己「啊」一声——把真相藏在一个动作、一件旧物、一句
对不上的话里,让读者在读完那一刻回头发现前三章的细节全都变味了。本章要掀翻的
判断必须真的被掀翻:给足推翻它的实证,别只是让角色嘴上宣布。"""

_BELIEVED_HEADER = "■ 读者目前相信(需要在恰当处被推翻或动摇):"
_HELD_HEADER = "■ 读者还不知道的底牌(可以选一张在此翻出来):"
_ASYM_HEADER = "■ 角色知道而读者不知道的(信息差,紧张感来源):"


def render_twist_block(view: ReaderView, db: Session) -> str:
    """反转预备块。非转折章、或没有任何可用的对照素材 → 空串(零 token)。

    advisory 语义:这是**提示**(「你可以掀翻这个」),不是门禁要求。
    找不到可掀翻的素材时不硬编——编出来的「读者以为 A」如果读者根本没这么想过,
    反而会把模型带歪。
    """
    if not view.is_twist:
        return ""

    names = _entity_names(
        db, {f.entity_id for f in view.held} | {f.entity_id for f, _, _ in view.asymmetries}
    )

    believed = _believed_items(view, db)
    held = view.held[:_MAX_TWISTS]
    asym = view.asymmetries[:2]

    if not believed and not held and not asym:
        return ""

    signals = "· 转折信号:" + "、".join(view.twist_signals) + "\n"

    believed_block = ""
    if believed:
        believed_block = _BELIEVED_HEADER + "\n" + "\n".join(
            f"· {text}" for text in believed
        )
    held_block = ""
    if held:
        held_block = _HELD_HEADER + "\n" + "\n".join(
            f"· {names.get(f.entity_id, '某人')}:{f.content}(第{f.valid_from}章起就存在)"
            for f in held
        )
    asymmetry_block = ""
    if asym:
        asymmetry_block = _ASYM_HEADER + "\n" + "\n".join(
            f"· {who} 自第{ch}章起知道:{f.content}" for f, who, ch in asym
        )
    return _TWIST_BLOCK.format(
        signals=signals,
        believed_block=believed_block,
        held_block=held_block,
        asymmetry_block=asymmetry_block,
    ).strip()


def _believed_items(view: ReaderView, db: Session) -> list[str]:
    """读者目前相信的东西:最近披露的关键事实。

    取「披露章最新鲜」的 critical/major 事实——记忆新鲜度是读者的真实状态:第 3 章
    知道的事到第 20 章已经模糊,而第 18 章刚知道的事才是他此刻用来判断的依据。
    view.known 已按 (披露章倒序, 重要度) 排好,这里只过滤重要度后截断。
    """
    names = _entity_names(db, {f.entity_id for f, _ in view.known})
    items: list[str] = []
    for f, ch in view.known:
        if f.importance not in _HELD_IMPORTANCE:
            continue
        items.append(
            f"第{ch}章起,读者已知道 {names.get(f.entity_id, '某人')}:{f.content}"
        )
        if len(items) >= _MAX_TWISTS:
            break
    return items


# ---------- 披露节奏(全书视角) ----------

@dataclass
class DisclosureRhythm:
    """全书披露节奏:每章读者新增知道了多少条。"""

    per_chapter: dict[int, int] = field(default_factory=dict)
    dry_runs: list[tuple[int, int]] = field(default_factory=list)   # (起始章, 连续章数)
    bursts: list[tuple[int, int]] = field(default_factory=list)     # (章号, 披露条数)
    total: int = 0


# 连续多少章零披露算「憋太久」:悬念压着不放,读者会忘掉它在压什么。
_DRY_RUN_ALERT = 6
# 单章披露超过多少条算「填鸭」:一次塞太多,读者记不住也叫不出好。
_BURST_ALERT = 5


def disclosure_rhythm(
    db: Session, project_id: int, *, up_to_chapter: int = 0
) -> DisclosureRhythm:
    """算全书读者披露节奏(稳态诊断,零 LLM)。

    用途:提示「悬念憋太久」与「一次爆太多」。节奏问题很难靠单章 prompt 发现——
    模型只看得到本章与最近几章的尾巴,它不知道前面已经 8 章没给读者任何新东西了。
    """
    q = db.query(KnowledgeState).filter(
        KnowledgeState.project_id == project_id,
        KnowledgeState.knower == "reader",
    )
    if up_to_chapter > 0:
        q = q.filter(KnowledgeState.known_from_chapter <= up_to_chapter)
    rows = q.all()

    per: dict[int, int] = {}
    for ks in rows:
        ch = int(ks.known_from_chapter)
        per[ch] = per.get(ch, 0) + 1

    rhythm = DisclosureRhythm(per_chapter=per, total=len(rows))
    if not per:
        return rhythm

    span = max(per) if up_to_chapter <= 0 else max(up_to_chapter, max(per))
    run_start = None
    for ch in range(1, span + 1):
        if per.get(ch, 0) == 0:
            if run_start is None:
                run_start = ch
        else:
            if run_start is not None and ch - run_start >= _DRY_RUN_ALERT:
                rhythm.dry_runs.append((run_start, ch - run_start))
            run_start = None
    if run_start is not None and span + 1 - run_start >= _DRY_RUN_ALERT:
        rhythm.dry_runs.append((run_start, span + 1 - run_start))

    rhythm.bursts = sorted(
        ((ch, n) for ch, n in per.items() if n > _BURST_ALERT),
        key=lambda t: t[0],
    )
    return rhythm


def render_rhythm_note(rhythm: DisclosureRhythm, chapter_number: int) -> str:
    """披露节奏提示(advisory,给审校报告用,不进生成 prompt)。

    生成 prompt 里塞「你前面憋了 8 章」没有可操作性——那是结构问题,该由作者在
    看板上决定「是不是该放点东西了」,而不是靠模型临时加一句解释。
    """
    notes: list[str] = []
    for start, length in rhythm.dry_runs:
        # 空窗一直延续到「已写到的那一章」,所以界是 <= 而不是 <:
        # 第 2-7 章连续 6 章无披露,在第 7 章这个时点上就是当前状态,不是过去时。
        if start + length - 1 <= chapter_number:
            notes.append(
                f"第{start}-{start + length - 1}章连续 {length} 章没有向读者披露任何新信息,"
                "悬念一直悬着没动——读者容易忘掉自己在等什么。"
            )
    for ch, n in rhythm.bursts:
        if ch <= chapter_number:
            notes.append(
                f"第{ch}章一次披露了 {n} 条新信息,读者可能消化不良,"
                "考虑把其中一部分拆到相邻章。"
            )
    return "\n".join(notes)
