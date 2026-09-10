# tests/test_reader_knowledge.py
# -*- coding: utf-8 -*-
"""读者认知集(§1.4)测试:认知地图派生、转折判定、反转预备渲染、披露节奏。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Entity, Fact, KnowledgeState, Outline, Project
from app.engines.consistency.reader_knowledge import (
    _BURST_ALERT,
    _DRY_RUN_ALERT,
    build_reader_view,
    disclosure_rhythm,
    is_twist_chapter,
    render_rhythm_note,
    render_twist_block,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _project(db, **kw) -> Project:
    p = Project(title="测试书", target_chapters=kw.pop("target_chapters", 30), **kw)
    db.add(p)
    db.flush()
    return p


def _entity(db, pid, name, *, retired=False) -> Entity:
    e = Entity(
        project_id=pid, entity_type="character", name=name,
        aliases=[], base_profile={}, retired=retired,
    )
    db.add(e)
    db.flush()
    return e


def _fact(db, pid, ent, content, *, valid_from=1, importance="major",
          fact_type="state", valid_until=None) -> Fact:
    f = Fact(
        project_id=pid, entity_id=ent.id, fact_type=fact_type, content=content,
        valid_from=valid_from, valid_until=valid_until, importance=importance,
        source_chapter=valid_from,
    )
    db.add(f)
    db.flush()
    return f


def _disclose(db, pid, fact, chapter, *, knower="reader", state="known") -> KnowledgeState:
    ks = KnowledgeState(
        project_id=pid, fact_id=fact.id, knower=knower,
        known_from_chapter=chapter, knower_state=state,
    )
    db.add(ks)
    db.flush()
    return ks


def _outline(db, pid, chapter, **kw) -> Outline:
    o = Outline(project_id=pid, chapter_number=chapter, title=f"第{chapter}章", **kw)
    db.add(o)
    db.flush()
    return o


# ---------- 转折章判定 ----------

def test_star_level_marks_twist(db):
    pid = _project(db).id
    o = _outline(db, pid, 5, plot_twist_level="★★★★☆")
    assert is_twist_chapter(o) is True


def test_low_stars_is_not_twist(db):
    pid = _project(db).id
    o = _outline(db, pid, 5, plot_twist_level="★★☆☆☆")
    assert is_twist_chapter(o) is False


def test_strong_word_marks_twist(db):
    pid = _project(db).id
    o = _outline(db, pid, 5, plot_twist_level="强")
    assert is_twist_chapter(o) is True


def test_low_word_does_not_mark_twist(db):
    """「高强度但低反转」这种写法不该被算成转折章。"""
    pid = _project(db).id
    o = _outline(db, pid, 5, plot_twist_level="低")
    assert is_twist_chapter(o) is False


def test_role_word_marks_twist(db):
    pid = _project(db).id
    o = _outline(db, pid, 5, chapter_role="关键转折")
    assert is_twist_chapter(o) is True


def test_summary_word_marks_twist_as_fallback(db):
    """老蓝图没填认知颠覆字段,概要里带转折词兜底。"""
    pid = _project(db).id
    o = _outline(db, pid, 5, summary="他终于揭露了当年的真相")
    assert is_twist_chapter(o) is True


def test_neutral_chapter_is_not_twist(db):
    pid = _project(db).id
    o = _outline(db, pid, 5, chapter_role="过渡", summary="两人赶路,夜宿客栈")
    assert is_twist_chapter(o) is False


def test_no_outline_is_not_twist(db):
    assert is_twist_chapter(None) is False


# ---------- 认知地图派生 ----------

def test_disclosed_fact_lands_in_known(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "左手有旧伤", valid_from=2)
    _disclose(db, pid, f, 2)

    view = build_reader_view(db, pid, 5)
    assert [x.id for x, _ in view.known] == [f.id]
    assert view.known[0][1] == 2


def test_future_disclosure_not_known_yet(db):
    """第 8 章才披露的事,第 5 章时读者还不知道。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "其实是皇子", valid_from=3)
    _disclose(db, pid, f, 8)

    view = build_reader_view(db, pid, 5)
    assert view.known == []


def test_undisclosed_key_fact_becomes_held_card(db):
    pid = _project(db).id
    ent = _entity(db, pid, "国师")
    f = _fact(db, pid, ent, "国师才是主谋", valid_from=2, importance="critical")

    view = build_reader_view(db, pid, 6)
    assert [x.id for x in view.held] == [f.id]


def test_minor_undisclosed_is_not_a_held_card(db):
    """鸡毛蒜皮的未披露事实不构成反转素材。"""
    pid = _project(db).id
    ent = _entity(db, pid, "路人")
    _fact(db, pid, ent, "他今天没吃饭", valid_from=2, importance="minor")

    view = build_reader_view(db, pid, 6)
    assert view.held == []


def test_new_fact_this_chapter_is_not_held(db):
    """本章才产生的事实谈不上「瞒着读者」——它刚发生。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    _fact(db, pid, ent, "本章刚断臂", valid_from=6, importance="critical")

    view = build_reader_view(db, pid, 6)
    assert view.held == []


def test_expired_fact_excluded(db):
    """已失效的事实不该出现在认知地图里。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "曾经中毒", valid_from=1, valid_until=3, importance="critical")

    view = build_reader_view(db, pid, 6)
    assert view.held == []
    assert view.known == []


def test_retired_entity_facts_excluded(db):
    pid = _project(db).id
    ent = _entity(db, pid, "已退场者", retired=True)
    _fact(db, pid, ent, "他是真凶", valid_from=1, importance="critical")

    view = build_reader_view(db, pid, 6)
    assert view.held == []


def test_entity_asymmetry_detected(db):
    """角色知道、读者不知道 → 信息差。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    knower = _entity(db, pid, "沈砚")
    f = _fact(db, pid, ent, "玉佩是假的", valid_from=2)
    _disclose(db, pid, f, 3, knower=str(knower.id))

    view = build_reader_view(db, pid, 6)
    assert len(view.asymmetries) == 1
    _, who, ch = view.asymmetries[0]
    assert who == "沈砚"
    assert ch == 3


def test_symmetry_not_counted_when_reader_also_knows(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    knower = _entity(db, pid, "沈砚")
    f = _fact(db, pid, ent, "玉佩是假的", valid_from=2)
    _disclose(db, pid, f, 3, knower=str(knower.id))
    _disclose(db, pid, f, 4)  # 读者后来也知道了

    view = build_reader_view(db, pid, 6)
    assert view.asymmetries == []
    assert [x.id for x, _ in view.known] == [f.id]


def test_earliest_disclosure_chapter_wins(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "旧伤", valid_from=1)
    _disclose(db, pid, f, 4)
    _disclose(db, pid, f, 2)  # 重复记录,取最早

    view = build_reader_view(db, pid, 6)
    assert view.known[0][1] == 2


def test_view_carries_twist_signals(db):
    pid = _project(db).id
    o = _outline(db, pid, 6, plot_twist_level="★★★★★")
    view = build_reader_view(db, pid, 6, outline=o)
    assert view.is_twist is True
    assert view.twist_signals


# ---------- 反转预备渲染 ----------

def test_render_block_for_twist_chapter(db):
    pid = _project(db).id
    ent = _entity(db, pid, "国师")
    held = _fact(db, pid, ent, "国师才是主谋", valid_from=2, importance="critical")
    o = _outline(db, pid, 6, plot_twist_level="★★★★☆")

    view = build_reader_view(db, pid, 6, outline=o)
    block = render_twist_block(view, db)
    assert "反转预备" in block
    assert "国师才是主谋" in block
    # 关键:必须明确要求「不要靠人物开口解释真相」
    assert "不要靠人物开口解释真相" in block


def test_render_empty_for_non_twist_chapter(db):
    """非转折章一个 token 都不花。"""
    pid = _project(db).id
    ent = _entity(db, pid, "国师")
    _fact(db, pid, ent, "国师才是主谋", valid_from=2, importance="critical")
    o = _outline(db, pid, 6, chapter_role="过渡")

    view = build_reader_view(db, pid, 6, outline=o)
    assert render_twist_block(view, db) == ""


def test_render_empty_when_no_material(db):
    """转折章但库里什么素材都没有 → 空串,不凭空编「读者以为 A」。"""
    pid = _project(db).id
    o = _outline(db, pid, 6, plot_twist_level="★★★★☆")
    view = build_reader_view(db, pid, 6, outline=o)
    assert render_twist_block(view, db) == ""


def test_render_includes_asymmetry(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    knower = _entity(db, pid, "沈砚")
    f = _fact(db, pid, ent, "玉佩是假的", valid_from=2, importance="major")
    _disclose(db, pid, f, 3, knower=str(knower.id))
    o = _outline(db, pid, 6, plot_twist_level="★★★★★")

    view = build_reader_view(db, pid, 6, outline=o)
    block = render_twist_block(view, db)
    assert "信息差" in block
    assert "沈砚" in block


def test_render_mentions_believed_disclosure(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "林昭是孤儿", valid_from=1, importance="major")
    _disclose(db, pid, f, 2)
    o = _outline(db, pid, 6, plot_twist_level="★★★★☆")

    view = build_reader_view(db, pid, 6, outline=o)
    block = render_twist_block(view, db)
    assert "读者目前相信" in block
    assert "林昭是孤儿" in block


def test_render_caps_held_cards(db):
    """一次掀翻太多读者只会晕:底牌最多 2 张。"""
    from app.engines.consistency.reader_knowledge import _MAX_TWISTS

    pid = _project(db).id
    ent = _entity(db, pid, "国师")
    for i in range(5):
        _fact(db, pid, ent, f"秘密{i}", valid_from=2, importance="critical")
    o = _outline(db, pid, 6, plot_twist_level="★★★★☆")

    view = build_reader_view(db, pid, 6, outline=o)
    block = render_twist_block(view, db)
    assert block.count("章起就存在") <= _MAX_TWISTS


# ---------- 披露节奏 ----------

def test_rhythm_counts_per_chapter(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    for ch, n in ((1, 2), (4, 1)):
        for i in range(n):
            f = _fact(db, pid, ent, f"事{ch}-{i}", valid_from=ch)
            _disclose(db, pid, f, ch)

    r = disclosure_rhythm(db, pid, up_to_chapter=10)
    assert r.per_chapter == {1: 2, 4: 1}
    assert r.total == 3


def test_rhythm_detects_dry_run(db):
    """连续多章零披露 → 憋太久告警。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "开头的事", valid_from=1)
    _disclose(db, pid, f, 1)

    r = disclosure_rhythm(db, pid, up_to_chapter=1 + _DRY_RUN_ALERT)
    assert r.dry_runs
    assert r.dry_runs[0][0] == 2


def test_rhythm_detects_burst(db):
    """单章爆出一大堆 → 填鸭告警。"""
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    for i in range(_BURST_ALERT + 2):
        f = _fact(db, pid, ent, f"爆料{i}", valid_from=3)
        _disclose(db, pid, f, 3)

    r = disclosure_rhythm(db, pid, up_to_chapter=5)
    assert r.bursts == [(3, _BURST_ALERT + 2)]


def test_rhythm_empty_is_safe(db):
    pid = _project(db).id
    r = disclosure_rhythm(db, pid, up_to_chapter=5)
    assert r.total == 0
    assert r.per_chapter == {}
    assert r.dry_runs == []


def test_rhythm_note_mentions_dry_run(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    f = _fact(db, pid, ent, "开头的事", valid_from=1)
    _disclose(db, pid, f, 1)

    r = disclosure_rhythm(db, pid, up_to_chapter=1 + _DRY_RUN_ALERT)
    note = render_rhythm_note(r, 1 + _DRY_RUN_ALERT)
    assert "没有向读者披露" in note


def test_rhythm_note_empty_when_healthy(db):
    pid = _project(db).id
    ent = _entity(db, pid, "林昭")
    for ch in range(1, 6):
        f = _fact(db, pid, ent, f"事{ch}", valid_from=ch)
        _disclose(db, pid, f, ch)

    r = disclosure_rhythm(db, pid, up_to_chapter=5)
    assert render_rhythm_note(r, 5) == ""


# ---------- prompt 接线钉 ----------

def test_draft_prompts_carry_twist_placeholder():
    """章级与场景级草稿 prompt 都得留反转预备位:少一处,场景级模式下反转就哑了。"""
    from app.prompts.chapter import CHAPTER_DRAFT_PROMPT
    from app.prompts.scene import SCENE_DRAFT_PROMPT

    assert "{twist_prep}" in CHAPTER_DRAFT_PROMPT
    assert "{twist_prep}" in SCENE_DRAFT_PROMPT
