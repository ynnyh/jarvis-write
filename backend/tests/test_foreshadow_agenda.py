# tests/test_foreshadow_agenda.py
# -*- coding: utf-8 -*-
"""伏笔日程编排测试(纯确定性,零 LLM)。

钉住的核心契约:
1. 逾期最久的先被排上 —— 「欠债先还」是防烂尾的根本;
2. 一章最多 2 条硬性回收 —— 塞太多会变成「清账章」,悬念一次性抽空;
3. earliest_payoff_chapter 是硬下限 —— 作者说不许提前收就不许收;
4. 逾期的排不进本章也必须出现在 still_pending(不能让模型以为不存在);
5. 活跃数超容时给明确禁令,而不是让模型自己判断;
6. 债务体检的数据面要真实(欠了多少条、最久欠了多久)。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Foreshadowing, Project
from app.engines.consistency.foreshadow_agenda import (
    MAX_PAYOFF_PER_CHAPTER,
    active_cap,
    admission_note,
    book_debt,
    build_agenda,
    render_agenda_block,
)


def _db():
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _project(db, chapters: int = 30) -> Project:
    p = Project(title="伏笔书", topic="修仙", genre="仙侠", target_chapters=chapters)
    db.add(p)
    db.commit()
    return p


def _foreshadow(
    db, pid: int, desc: str, *, planted: int, payoff: int | None = None,
    earliest: int | None = None, importance: str = "major",
    status: str = "planted",
) -> Foreshadowing:
    f = Foreshadowing(
        project_id=pid, description=desc, chapter_planted=planted,
        expected_payoff_chapter=payoff, earliest_payoff_chapter=earliest,
        importance=importance, status=status,
    )
    db.add(f)
    db.commit()
    return f


# ---------- 容量 ----------

def test_cap_scales_with_volume():
    """上限按体量动态:长篇容许更多,短篇不会归零。"""
    assert active_cap(10) >= 2         # 短篇也要埋得起来
    assert active_cap(20) == 3
    assert active_cap(40) == 6
    assert active_cap(200) > active_cap(100)


def test_cap_never_zero():
    assert active_cap(0) >= 2
    assert active_cap(1) >= 2


# ---------- 排程 ----------

def test_overdue_is_scheduled_first():
    """逾期最久的优先 —— 欠债先还。"""
    db = _db()
    p = _project(db, 20)
    _foreshadow(db, p.id, "新欠的", planted=1, payoff=9)
    _foreshadow(db, p.id, "欠很久的", planted=1, payoff=3)
    agenda = build_agenda(db, p.id, 10, target_chapters=20)
    assert agenda.must_payoff[0].description == "欠很久的"


def test_importance_breaks_tie():
    """同逾期程度时,critical 优先(critical 悬空的代价最大)。"""
    db = _db()
    p = _project(db, 20)
    _foreshadow(db, p.id, "minor 的", planted=1, payoff=5, importance="minor")
    _foreshadow(db, p.id, "critical 的", planted=1, payoff=5, importance="critical")
    agenda = build_agenda(db, p.id, 10, target_chapters=20)
    assert agenda.must_payoff[0].description == "critical 的"


def test_payoff_per_chapter_capped():
    """一章最多 2 条硬性回收,超出的进 still_pending。"""
    db = _db()
    p = _project(db, 60)
    for i in range(5):
        _foreshadow(db, p.id, f"线索{i}", planted=1, payoff=5)
    agenda = build_agenda(db, p.id, 10, target_chapters=60)
    assert len(agenda.must_payoff) == MAX_PAYOFF_PER_CHAPTER
    assert len(agenda.still_pending) == 3


def test_earliest_payoff_is_a_hard_floor():
    """作者设的「最早不能早于」是硬下限,未到不许收。"""
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "还不到时候", planted=1, payoff=5, earliest=20)
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    assert agenda.must_payoff == []
    assert agenda.still_pending == []


def test_future_payoff_goes_to_reinforce():
    """快到期的(2 章内)进强化列表,不硬收。"""
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "快到期", planted=1, payoff=12)
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    assert agenda.must_payoff == []
    assert [f.description for f in agenda.should_reinforce] == ["快到期"]


def test_long_hanging_without_date_gets_reinforced():
    """埋了很久却没定回收章的:至少强化一次,别沉底。"""
    db = _db()
    p = _project(db, 60)
    _foreshadow(db, p.id, "被遗忘的", planted=1, payoff=None)
    agenda = build_agenda(db, p.id, 20, target_chapters=60)
    assert [f.description for f in agenda.should_reinforce] == ["被遗忘的"]


def test_recent_without_date_is_left_alone():
    """刚埋下还没定回收章的,不用急着提醒。"""
    db = _db()
    p = _project(db, 60)
    _foreshadow(db, p.id, "刚埋的", planted=18, payoff=None)
    agenda = build_agenda(db, p.id, 20, target_chapters=60)
    assert agenda.should_reinforce == []
    assert agenda.must_payoff == []


def test_paid_off_excluded():
    """已回收的不进日程。"""
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "收过了", planted=1, payoff=5, status="paid_off")
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    assert agenda.must_payoff == []
    assert agenda.still_pending == []


# ---------- 准入 ----------

def test_at_capacity_flag():
    db = _db()
    p = _project(db, 20)  # cap = 3
    for i in range(3):
        _foreshadow(db, p.id, f"线索{i}", planted=1, payoff=50)
    agenda = build_agenda(db, p.id, 5, target_chapters=20)
    assert agenda.at_capacity is True
    assert agenda.active_count == 3
    assert agenda.cap == 3


def test_admission_note_bans_new_when_full():
    db = _db()
    p = _project(db, 20)
    for i in range(3):
        _foreshadow(db, p.id, f"线索{i}", planted=1, payoff=50)
    note = admission_note(db, p.id, target_chapters=20)
    assert "不要再埋新伏笔" in note
    assert "3/3" in note


def test_admission_note_allows_when_below_cap():
    db = _db()
    p = _project(db, 20)
    _foreshadow(db, p.id, "只有一条", planted=1, payoff=50)
    note = admission_note(db, p.id, target_chapters=20)
    assert "不要再埋" not in note
    assert "1/3" in note


# ---------- 渲染 ----------

def test_render_includes_responsibility_not_just_reminder():
    """渲染出来的必须是「必须兑现」的任务语气,不能只是提醒。"""
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "断剑的来历", planted=1, payoff=5)
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    block = render_agenda_block(agenda, 10)
    assert "必须兑现" in block
    assert "断剑的来历" in block
    assert "逾期" in block


def test_render_marks_critical():
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "主线真相", planted=1, payoff=5, importance="critical")
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    assert "★" in render_agenda_block(agenda, 10)


def test_render_empty_when_nothing_to_say():
    """没有排程任务且未超容时返回空串,不占 token。"""
    db = _db()
    p = _project(db, 30)
    agenda = build_agenda(db, p.id, 5, target_chapters=30)
    assert render_agenda_block(agenda, 5) == ""


def test_render_shows_capacity_ban():
    db = _db()
    p = _project(db, 20)
    for i in range(3):
        _foreshadow(db, p.id, f"线索{i}", planted=1, payoff=50)
    agenda = build_agenda(db, p.id, 5, target_chapters=20)
    block = render_agenda_block(agenda, 5)
    assert "上限" in block
    assert "不要再埋新伏笔" in block


# ---------- 债务体检 ----------

def test_debt_reports_real_numbers():
    db = _db()
    p = _project(db, 60)
    _foreshadow(db, p.id, "a", planted=1, payoff=5)    # 逾期 5
    _foreshadow(db, p.id, "b", planted=3, payoff=20)   # 未逾期
    debt = book_debt(
        db.query(Foreshadowing).filter(Foreshadowing.project_id == p.id).all(), 10
    )
    assert debt["active"] == 2
    assert debt["overdue"] == 1
    assert debt["longest_hanging"] == 9


def test_debt_serious_overdue_threshold():
    db = _db()
    p = _project(db, 60)
    _foreshadow(db, p.id, "欠 8 章", planted=1, payoff=2)
    debt = book_debt(
        db.query(Foreshadowing).filter(Foreshadowing.project_id == p.id).all(), 10
    )
    assert debt["serious"] >= 1


def test_debt_empty_is_safe():
    debt = book_debt([], 10)
    assert debt["active"] == 0 and debt["avg_hanging"] == 0.0


def test_agenda_carries_debt():
    db = _db()
    p = _project(db, 30)
    _foreshadow(db, p.id, "x", planted=1, payoff=5)
    agenda = build_agenda(db, p.id, 10, target_chapters=30)
    assert agenda.debt["active"] == 1
