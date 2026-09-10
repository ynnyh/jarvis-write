# app/engines/consistency/foreshadow_agenda.py
# -*- coding: utf-8 -*-
"""伏笔日程编排:把「到期提醒」升级为「硬性任务 + 准入控制 + 债务审计」。

问题(docs/15 §1.3):全行业最普遍的通病是**模型爱埋伏笔、不爱收**。
现有 `ForeshadowScheduler.reminder_block()` 只在临近/逾期时塞一行提醒——
模型完全可以无视它。到了第 80 章,你会有一堆悬空线索,读者记不住,作者也
收不回来。

这里把伏笔从「提醒」变成三件有约束的事:

  ① **准入控制**(写前):新增伏笔必须带「预期回收章 + 回收方式」,且同时活跃
     的伏笔数有上限。超限时 prompt 里明确禁止再埋——不是请求,是禁令。
     上限按体量动态算(每 20 章允许 3 条 major),短篇不会被逼着少埋伏笔,
     长篇也不会失控膨胀。
  ② **日程排程**(写前):这一章**必须**回收/推进哪几条,算出来写成硬性任务,
     而不只是「⏰临近」的提示。排程按「逾期最久 + 重要度最高」优先。
  ③ **健康度审计**(写后):算「伏笔债务」= 活跃数 / 回收率 / 平均悬空章数。
     超标时主动要求后续章腾出回收位。

零 LLM:
  全部是确定性计算。排程本来就是「谁的截止日到了」这种排序问题,不需要模型。
  把结构交给规则,模型才能专心写回收时的戏。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Foreshadowing

logger = logging.getLogger("jarvis-write.foreshadow_agenda")

# 重要度排序:越靠前越必须先回收
_IMPORTANCE_RANK = {"critical": 0, "major": 1, "minor": 2}

# 活跃伏笔数上限:按体量动态。每 20 章允许 3 条 major 当量——
# 短篇(10 章)≈ 1.5 条,长篇(200 章)= 30 条。少于这个数,伏笔不够撑悬念;
# 多于这个数,读者记不住,回收率必然崩。
_VOLUME_UNIT = 20
_MAJOR_PER_UNIT = 3
# 每章最多要求回收几条:一章塞太多回收会变成「清账章」——悬念突然全没了,
# 节奏反而崩。硬性任务一次最多 2 条,其余排在后续章。
MAX_PAYOFF_PER_CHAPTER = 2
# 每章最多建议推进(强化)几条
MAX_REINFORCE_PER_CHAPTER = 3

# 逾期多少章算「严重拖欠」——进入审计的告警位
_SERIOUS_OVERDUE = 5


def active_cap(target_chapters: int) -> int:
    """按体量算活跃伏笔数上限(至少 2,短篇也要能埋得起来)。"""
    units = max(1, int(target_chapters or 0) // _VOLUME_UNIT)
    return max(2, units * _MAJOR_PER_UNIT)


def _rank(f: Foreshadowing) -> int:
    return _IMPORTANCE_RANK.get(str(f.importance or "major"), 1)


def _overdue_chapters(f: Foreshadowing, chapter_number: int) -> int:
    """逾期了几章(未到期返回负数,即还剩几章)。"""
    if f.expected_payoff_chapter is None:
        return 0
    return chapter_number - int(f.expected_payoff_chapter)


@dataclass
class Agenda:
    """一章的伏笔日程。"""

    # 必须在这一章兑现的(硬性任务,尽量真收)
    must_payoff: list[Foreshadowing] = field(default_factory=list)
    # 建议推进/强化的(给更多线索,还不急着收)
    should_reinforce: list[Foreshadowing] = field(default_factory=list)
    # 已逾期但排不进本章的(明确告知模型欠着账)
    still_pending: list[Foreshadowing] = field(default_factory=list)
    # 是否已超容:True 时 prompt 里禁止再新增伏笔
    at_capacity: bool = False
    active_count: int = 0
    cap: int = 0
    debt: dict[str, Any] = field(default_factory=dict)


def build_agenda(
    db: Session,
    project_id: int,
    chapter_number: int,
    *,
    target_chapters: int = 0,
) -> Agenda:
    """算出一章的伏笔日程(全部确定性,不花 LLM)。

    排程规则:
      1. 逾期最久的优先(欠债先还,避免烂尾);
      2. 同逾期程度取重要度高的(critical 的悬空代价最大);
      3. `earliest_payoff_chapter` 未到的不能提前收(作者设的硬下限);
      4. 一章最多 2 条硬性回收,超出的列入 still_pending 明确告知欠账。
    """
    active = (
        db.query(Foreshadowing)
        .filter(
            Foreshadowing.project_id == project_id,
            Foreshadowing.status.in_(("planted", "reinforced")),
        )
        .all()
    )
    cap = active_cap(target_chapters)

    # 可以收的:预期回收章已到(或没设预期但已埋了 20 章以上,该提醒作者考虑)
    collectable: list[Foreshadowing] = []
    reinforce: list[Foreshadowing] = []
    for f in active:
        earliest = f.earliest_payoff_chapter
        if earliest is not None and int(earliest) > chapter_number:
            continue  # 作者设的硬下限还没到
        exp = f.expected_payoff_chapter
        if exp is not None and int(exp) <= chapter_number:
            collectable.append(f)
        elif exp is not None and int(exp) <= chapter_number + 2:
            reinforce.append(f)
        elif exp is None and chapter_number - int(f.chapter_planted) >= 15:
            # 埋了很久却没定回收章:让模型至少强化一次,别沉底
            reinforce.append(f)

    # 逾期最久 → 重要度最高
    collectable.sort(key=lambda f: (-_overdue_chapters(f, chapter_number), _rank(f)))
    reinforce.sort(key=lambda f: (_rank(f), int(f.chapter_planted)))

    must = collectable[:MAX_PAYOFF_PER_CHAPTER]
    pending = collectable[MAX_PAYOFF_PER_CHAPTER:]

    return Agenda(
        must_payoff=must,
        should_reinforce=reinforce[:MAX_REINFORCE_PER_CHAPTER],
        still_pending=pending,
        at_capacity=len(active) >= cap,
        active_count=len(active),
        cap=cap,
        debt=book_debt(active, chapter_number),
    )


def book_debt(active: list[Foreshadowing], chapter_number: int) -> dict[str, Any]:
    """伏笔债务体检:活跃数 / 逾期数 / 平均悬空章数 / 严重拖欠数。

    这是「写后审计」的数据面——前端健康报告与评测轨消费它。
    不解释、只报事实:欠了多少条、最久欠了多久。
    """
    if not active:
        return {
            "active": 0, "overdue": 0, "serious": 0,
            "avg_hanging": 0.0, "longest_hanging": 0,
        }
    hangings = [
        max(0, chapter_number - int(f.chapter_planted))
        for f in active
    ]
    overdue = [
        f for f in active
        if f.expected_payoff_chapter is not None
        and int(f.expected_payoff_chapter) < chapter_number
    ]
    serious = [
        f for f in overdue
        if -_overdue_chapters(f, chapter_number) < -_SERIOUS_OVERDUE
    ]
    return {
        "active": len(active),
        "overdue": len(overdue),
        "serious": len(serious),
        "avg_hanging": round(sum(hangings) / len(hangings), 1),
        "longest_hanging": max(hangings),
    }


# 注入正文 prompt 的日程块。与旧的 reminder_block 的区别:
# 旧的是「提醒」(模型可以无视),这里的「必须回收」是任务清单。
_AGENDA_BLOCK = """\
【本章伏笔日程(排程算出来的硬性任务,不是可选提醒)】
{must_block}
{reinforce_block}
{pending_block}
{capacity_block}
收伏笔要比埋伏笔好看:别让角色干巴巴地解释「原来那件事是这样」,把揭晓放在
一次冲突、一个动作、或一句没说完的话里。读者要的是「原来如此」的那一下战栗,
不是一条被清理的待办。"""

_MUST_HEADER = "■ 本章必须兑现(至少完成第一条):"
_REINFORCE_HEADER = "□ 本章可顺手推进(加一层线索,不急着收):"
_PENDING_HEADER = "· 还欠着的(本章排不下,下章继续,别当成不存在):"
_CAPACITY_LINE = (
    "⚠️ 当前活跃伏笔 {active}/{cap} 条,已到上限:{ban}"
)


def render_agenda_block(agenda: Agenda, chapter_number: int) -> str:
    """日程 → prompt 注入块。无内容时返回 ""(不占 token)。"""
    if not agenda.must_payoff and not agenda.should_reinforce and not agenda.at_capacity:
        return ""

    def _line(f: Foreshadowing) -> str:
        exp = f.expected_payoff_chapter
        overdue = _overdue_chapters(f, chapter_number)
        timing = ""
        if exp is not None:
            timing = (
                f"(第{f.chapter_planted}章埋,预期第{exp}章收"
                + (f",已逾期 {overdue} 章)" if overdue > 0 else ")")
            )
        else:
            timing = f"(第{f.chapter_planted}章埋,尚未定回收章)"
        rank = "★" if str(f.importance) == "critical" else ""
        return f"{rank}{f.description} {timing}"

    parts = []
    if agenda.must_payoff:
        parts.append(_MUST_HEADER + "\n" + "\n".join(_line(f) for f in agenda.must_payoff))
    if agenda.should_reinforce:
        parts.append(
            _REINFORCE_HEADER + "\n"
            + "\n".join(_line(f) for f in agenda.should_reinforce)
        )
    if agenda.still_pending:
        parts.append(
            _PENDING_HEADER + "\n"
            + "\n".join(_line(f) for f in agenda.still_pending[:5])
        )
    capacity_block = ""
    if agenda.at_capacity:
        capacity_block = _CAPACITY_LINE.format(
            active=agenda.active_count,
            cap=agenda.cap,
            ban="本章不要再埋新伏笔,先把欠着的收回来",
        )
    return _AGENDA_BLOCK.format(
        must_block=parts[0] if len(parts) > 0 else "",
        reinforce_block=parts[1] if len(parts) > 1 else "",
        pending_block=parts[2] if len(parts) > 2 else "",
        capacity_block=capacity_block,
    ).strip()


def admission_note(
    db: Session, project_id: int, *, target_chapters: int = 0
) -> str:
    """写前准入提示:还能不能埋新伏笔。

    超容时给明确的禁令(而不是让模型自己判断)。这条要单独注入蓝图/prompt,
    因为「这一章该不该埋新伏笔」是结构决策,不该指望模型自觉。
    """
    active = (
        db.query(Foreshadowing)
        .filter(
            Foreshadowing.project_id == project_id,
            Foreshadowing.status.in_(("planted", "reinforced")),
        )
        .count()
    )
    cap = active_cap(target_chapters)
    if active < cap:
        return f"(当前活跃伏笔 {active}/{cap} 条,还可新增)"
    return (
        f"⚠️ 活跃伏笔已达上限({active}/{cap}):本章**不要再埋新伏笔**。"
        "新线索只能是对既有伏笔的强化,或用来推进/回收——"
        "埋了不收的线索会拖垮读者的记忆负担。"
    )
