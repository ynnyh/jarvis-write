# tests/test_timeline.py
# -*- coding: utf-8 -*-
"""全书剧情时间线测试(mock LLM,无需 API key)。

验证点(docs/08 §7 P2-⑨ 轻量落地):
- book_timeline:只收有效契约(提取 ok + 指纹对应当前正文);无契约/失败/
  指纹失效的章自然断档;按章号升序
- timeline_block:upto 过滤(不含本章)、最近 N 条截断、空占位文案、跳跃提示渲染
- prompt 注入:门禁检查(check_chapter)与写前审核(preflight_chapter)的
  prompt 带全书时间线块
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

CONTRACT = {
    "in_story_time": "第三日 深夜",
    "location": "破庙内",
    "scene_continues": False,
    "characters": [
        {
            "name": "沈墨", "location": "破庙内", "physical": None,
            "emotional": "疲惫", "doing": "刚入睡", "knows": [],
            "unresolved_intent": None,
        }
    ],
    "open_threads": [],
    "time_jump_hint": "next_morning",
}
CONTRACT_JSON = json.dumps(CONTRACT, ensure_ascii=False)

CH1_TEXT = "夜深了,沈墨在破庙里睡去。" * 20
CH2_TEXT = "沈墨在破庙里醒来,看着篝火发呆。" * 20


def _make_db(chapters: int = 2, stale_ch2: bool = False, ch3_no_contract: bool = True):
    """独立内存库:一个项目 + N 章正文;第 1 章有效契约,第 2 章(可选)指纹失效契约。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="时间线测试书", target_chapters=chapters,
                      target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    for n in range(1, chapters + 1):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=f"第{n}章",
            chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
        ))
        text = CH1_TEXT if n == 1 else CH2_TEXT
        ch = Chapter(
            project_id=project.id, outline_id=n, chapter_number=n,
            final_content=text + str(n), word_count=len(text), status="approved",
        )
        db.add(ch)
        db.flush()
        if n == 1:
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash=content_hash(text + str(n)), extract_status="ok",
            ))
        elif n == 2 and stale_ch2:
            # 指纹对不上当前正文:契约失效,不应进时间线
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash="stale-hash-0000", extract_status="ok",
            ))
        elif n == 2:
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash=content_hash(text + str(n)), extract_status="ok",
            ))
        # 第 3 章起默认无契约(老书断档)
    db.commit()
    return db, project


class _Adapter:
    """固定返回一条回复的假 LLM,记录全部 prompt。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


# ---------- book_timeline:只收有效契约 ----------
def test_book_timeline_collects_fresh_contracts_only():
    from app.engines.timeline import book_timeline

    db, project = _make_db(chapters=3, stale_ch2=True)
    items = book_timeline(db, project.id)
    # ch1 有效;ch2 指纹失效跳过;ch3 无契约跳过
    assert [i["chapter"] for i in items] == [1]
    assert items[0]["in_story_time"] == "第三日 深夜"
    assert items[0]["location"] == "破庙内"
    assert items[0]["time_jump_hint"] == "next_morning"


# ---------- timeline_block:upto 过滤 / 截断 / 占位 ----------
def test_timeline_block_upto_excludes_current_and_empty_placeholder():
    from app.engines.timeline import timeline_block

    db, project = _make_db()
    # 第 2 章视角:只见第 1 章
    block = timeline_block(db, project.id, upto=2)
    assert "第1章末:第三日 深夜 @ 破庙内" in block
    assert "(下章跳跃:next_morning)" in block
    # 第 1 章视角:前面什么都没有 → 占位文案
    assert timeline_block(db, project.id, upto=1).startswith("(无全书时间线")


def test_timeline_block_truncates_to_recent_entries():
    from app.engines.timeline import _PROMPT_MAX_ENTRIES, timeline_block

    db, project = _make_db(chapters=20)
    # 给 2-20 章都补有效契约(内容同上,指纹按各章正文算)
    from app.db.models import Chapter, ChapterState
    from app.engines.editorial import content_hash

    for ch in db.query(Chapter).filter(Chapter.chapter_number > 2):
        db.add(ChapterState(
            chapter_id=ch.id, contract=CONTRACT_JSON,
            content_hash=content_hash(ch.final_content), extract_status="ok",
        ))
    db.commit()

    block = timeline_block(db, project.id, upto=99)
    lines = [ln for ln in block.splitlines() if ln.startswith("第")]
    assert len(lines) == _PROMPT_MAX_ENTRIES
    assert "第20章末" in block  # 最近的保留
    assert "第5章末" not in block  # 超出窗口的被截掉


# ---------- prompt 注入:门禁 + 写前审核 ----------
def test_check_chapter_prompt_includes_timeline():
    from app.engines.consistency import checker as checker_mod

    db, project = _make_db()
    adapter = _Adapter('{"issues": []}')
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(checker_mod.check_chapter(db, project.id, 2, CH2_TEXT))

    assert len(adapter.prompts) == 1
    prompt = adapter.prompts[0]
    assert "全书剧情时间线" in prompt
    assert "第1章末:第三日 深夜" in prompt


def test_preflight_prompt_includes_timeline():
    from app.db.models import Outline
    from app.engines.consistency import preflight as preflight_mod

    db, project = _make_db()
    outline = db.query(Outline).filter(Outline.chapter_number == 2).first()
    adapter = _Adapter('{"warnings": []}')
    with patch.object(preflight_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(preflight_mod.preflight_chapter(db, project.id, 2, outline))

    assert len(adapter.prompts) == 1
    assert "全书剧情时间线" in adapter.prompts[0]
    assert "第1章末:第三日 深夜" in adapter.prompts[0]


# ==================== Phase 2:结构化故事时钟 ====================
# compute_clock(权威天数轴)/ check_story_clock(确定性算术校验,advisory)/
# timeline_block 权威轴渲染 / persist_clock_issues 落 source=clock 建议且不动 canon。


def _items(*triples):
    """[(chapter, story_day, days_remaining), ...] → timeline items(补齐其余键)。"""
    return [
        {
            "chapter": ch,
            "in_story_time": f"第{day}天" if day is not None else "未知",
            "story_day": day, "days_remaining": rem,
            "location": None, "scene_continues": False, "time_jump_hint": "none",
        }
        for ch, day, rem in triples
    ]


def _clocked(triples, dl):
    from app.engines.timeline import compute_clock

    return compute_clock(_items(*triples), dl)


# ---------- compute_clock:基准 / 派生 / 降级 ----------
def test_compute_clock_baseline_and_derivation():
    from app.engines.timeline import compute_clock
    from app.schemas.canon import CanonDeadline

    dl = CanonDeadline(name="任务倒计时", total_days=31, anchor_chapter=1)
    items = compute_clock(_items((1, 1, None), (2, 3, None), (3, 10, None)), dl)
    rem = {i["chapter"]: i["computed_remaining"] for i in items}
    assert rem == {1: 31, 2: 29, 3: 22}  # 31-(1-1) / 31-(3-1) / 31-(10-1)


def test_compute_clock_baseline_fallback_when_anchor_missing():
    from app.engines.timeline import compute_clock
    from app.schemas.canon import CanonDeadline

    # anchor=2 但第 2 章无 story_day → 取 ≥2 的最早有值章(第 3 章 day=5)作基准
    dl = CanonDeadline(name="倒计时", total_days=20, anchor_chapter=2)
    items = compute_clock(_items((1, 1, None), (2, None, None), (3, 5, None), (4, 8, None)), dl)
    rem = {i["chapter"]: i["computed_remaining"] for i in items}
    assert rem[1] is None   # 第 1 章在 anchor 之前,倒计时未开始,不算
    assert rem[2] is None   # 无 story_day
    assert rem[3] == 20     # 基准章:20-(5-5)
    assert rem[4] == 17     # 20-(8-5)


def test_compute_clock_degrades_to_none():
    from app.engines.timeline import compute_clock
    from app.schemas.canon import CanonDeadline

    base = _items((1, 1, None), (2, 3, None))
    # 无倒计时定义 → 全 None
    assert all(i["computed_remaining"] is None for i in compute_clock(base, None))
    # total_days=0 → 全 None
    dl0 = CanonDeadline(name="没天数", total_days=0, anchor_chapter=1)
    assert all(i["computed_remaining"] is None for i in compute_clock(base, dl0))
    # story_day 稀疏:有倒计时但该章无 story_day → 该章 None,不误报
    dl = CanonDeadline(name="倒计时", total_days=10, anchor_chapter=1)
    sparse = compute_clock(_items((1, 1, None), (2, None, None)), dl)
    assert {i["chapter"]: i["computed_remaining"] for i in sparse} == {1: 10, 2: None}


# ---------- check_story_clock:四类矛盾 + 合法不误报 ----------
def test_check_story_clock_day_regression_and_same_day_legal():
    from app.engines.timeline import check_story_clock
    from app.schemas.canon import CanonDeadline

    dl = CanonDeadline(name="x", total_days=31, anchor_chapter=1)
    # ① 第 2 章 day 从 5 倒退到 3
    issues = check_story_clock(_clocked([(1, 5, None), (2, 3, None)], dl), dl, 2)
    assert any("倒流" in i["description"] for i in issues)
    assert all(i["severity"] == "major" and i["type"] == "timeline" for i in issues)
    # 同一天(5→5)合法,不报倒流
    ok = check_story_clock(_clocked([(1, 5, None), (2, 5, None)], dl), dl, 2)
    assert not any("倒流" in i["description"] for i in ok)


def test_check_story_clock_remaining_increase():
    from app.engines.timeline import check_story_clock
    from app.schemas.canon import CanonDeadline

    dl = CanonDeadline(name="x", total_days=31, anchor_chapter=1)
    # ② days_remaining 从 20 反增到 25
    issues = check_story_clock(_clocked([(1, 5, 20), (2, 6, 25)], dl), dl, 2)
    assert any("反增" in i["description"] for i in issues)


def test_check_story_clock_mismatch_is_the_core_fix():
    from app.engines.timeline import check_story_clock
    from app.schemas.canon import CanonDeadline

    dl = CanonDeadline(name="任务倒计时", total_days=31, anchor_chapter=1)
    # ③ 第 5 章 day6(已过 5 天)权威应剩 26,正文却说还剩 20(#3 病)
    issues = check_story_clock(_clocked([(1, 1, 31), (5, 6, 20)], dl), dl, 5)
    mism = [i for i in issues if "口径不符" in i["description"]]
    assert mism, issues
    assert "应为 26 天" in mism[0]["description"]
    assert "还剩 20 天" in mism[0]["description"]


def test_check_story_clock_past_due():
    from app.engines.timeline import check_story_clock
    from app.schemas.canon import CanonDeadline

    dl = CanonDeadline(name="任务倒计时", total_days=10, anchor_chapter=1)
    # ④ day1 起算,第 3 章 day15 → 已过 14 > 10 → computed = -4 < 0
    issues = check_story_clock(_clocked([(1, 1, None), (3, 15, None)], dl), dl, 3)
    assert any("超期" in i["description"] for i in issues)


def test_check_story_clock_no_deadline_and_single_point_silent():
    from app.engines.timeline import check_story_clock, compute_clock
    from app.schemas.canon import CanonDeadline

    # 无倒计时 + 只有 story_day:① 倒流仍报(不依赖倒计时),②③④ 不报
    items = compute_clock(_items((1, 5, None), (2, 3, None)), None)
    issues = check_story_clock(items, None, 2)
    assert any("倒流" in i["description"] for i in issues)
    assert not any("口径" in i["description"] or "超期" in i["description"] for i in issues)
    # 单点(无 prev、无倒计时数据)→ 一条不报
    dl = CanonDeadline(name="x", total_days=31, anchor_chapter=1)
    assert check_story_clock(compute_clock(_items((1, 1, None)), dl), dl, 1) == []
    # focus 章不在时间线里 → 空
    assert check_story_clock(compute_clock(_items((1, 1, None)), dl), dl, 9) == []


# ---------- timeline_block:权威天数轴渲染 + persist_clock_issues 集成 ----------
def _make_clock_db():
    """内存库:带倒计时宪法的项目 + 两章(契约含 story_day/days_remaining)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="时钟测试书", target_chapters=5, target_words_per_chapter=3000,
        canon={"deadline": {"name": "任务倒计时", "total_days": 31, "anchor_chapter": 1}},
    )
    db.add(project)
    db.flush()
    # 第1章:第1天 剩31;第2章:第3天(已过2天)权威应剩 29
    for n, day, rem, t in ((1, 1, 31, "第一日"), (2, 3, 29, "第三日")):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=f"第{n}章",
            chapter_purpose="推进", summary="剧情", current_version=1,
        ))
        text = f"第{n}章正文。" * 40
        ch = Chapter(
            project_id=project.id, outline_id=n, chapter_number=n,
            final_content=text, word_count=len(text), status="approved",
        )
        db.add(ch)
        db.flush()
        contract = dict(CONTRACT, in_story_time=t, story_day=day, days_remaining=rem)
        db.add(ChapterState(
            chapter_id=ch.id, contract=json.dumps(contract, ensure_ascii=False),
            content_hash=content_hash(text), extract_status="ok",
        ))
    db.commit()
    return db, project


def test_timeline_block_renders_authoritative_axis():
    from app.engines.timeline import timeline_block

    db, project = _make_clock_db()
    block = timeline_block(db, project.id, upto=3)  # 第 3 章视角:见第 1、2 章
    assert "【倒计时·任务倒计时】" in block
    assert "共 31 天" in block
    assert "故事第 1 天" in block          # 第 1 章行
    assert "故事第 3 天" in block          # 第 2 章行
    assert "倒计时应剩 29 天" in block      # 第 2 章 computed = 31-(3-1)
    assert "截至第2章末" in block          # 表头带最新章权威值
    db.close()


def test_persist_clock_issues_flags_mismatch_and_leaves_canon_untouched():
    from app.db.models import Chapter, ChapterIssue, ChapterState, Project
    from app.engines.timeline import persist_clock_issues

    db, project = _make_clock_db()
    # 篡改第 2 章契约:正文声称还剩 20 天(权威轴应剩 29)→ 口径不符
    ch2 = db.query(Chapter).filter(
        Chapter.chapter_number == 2, Chapter.project_id == project.id
    ).first()
    row = db.query(ChapterState).filter(ChapterState.chapter_id == ch2.id).first()
    bad = json.loads(row.contract)
    bad["days_remaining"] = 20
    row.contract = json.dumps(bad, ensure_ascii=False)
    db.commit()

    persist_clock_issues(db, project.id, ch2, ch2.final_content)

    rows = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == ch2.id, ChapterIssue.source == "clock"
    ).all()
    assert rows, "应落一条 source=clock 的口径不符建议"
    assert any("口径不符" in r.description for r in rows)
    assert all(r.severity == "major" and r.status == "open" for r in rows)
    # advisory 不动 canon(唯一 canon 写路径是作者触发的 adopt-canon)
    fresh = db.get(Project, project.id)
    assert fresh.canon == {
        "deadline": {"name": "任务倒计时", "total_days": 31, "anchor_chapter": 1}
    }
    db.close()

