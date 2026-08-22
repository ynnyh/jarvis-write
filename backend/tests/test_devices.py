# tests/test_devices.py
# -*- coding: utf-8 -*-
"""常驻装置复现驱动测试(纯算术,无需 API key)。

验证点(长程一致性 Phase 3,修「女主有系统却多章消失」):
- device_states:断档章数只按**有契约的**章累计(无契约章是"无从得知",不算消失);
  从未出现过的装置不催不报(可能设定在后文才登场);名字宽容匹配
- devices_reminder_block:按 importance 分档阈值催场;无 canon 装置/未逾期 → 空串
- check_device_gaps:催场阈值之上再宽一章才软报;severity 按重要度;不到 blocker
- persist_device_issues:落 source="devices" 的 advisory,装置补回后旧告警自动消失,
  且不动 project.canon
- 契约字段:validate_contract 归一 devices_present;提取 prompt 带闭集清单
"""
from __future__ import annotations

import asyncio
import json

CONTRACT_BASE = {
    "in_story_time": "第一日 夜",
    "location": "出租屋",
    "scene_continues": False,
    "characters": [{
        "name": "林愿", "location": "出租屋", "physical": None, "emotional": "紧张",
        "doing": "盯着面板", "knows": [], "unresolved_intent": None,
    }],
    "open_threads": [],
    "time_jump_hint": "none",
}

CANON = {
    "devices": [
        {"name": "系统", "cadence": "每章都应有存在感", "importance": "critical"},
        {"name": "那枚玉佩", "cadence": "关键抉择处必出现", "importance": "minor"},
    ]
}


def _make_db(chapters, canon=CANON):
    """内存库:一个带 canon 装置的项目 + N 章正文与契约。

    chapters: [(章号, devices_present | None)];devices_present 为 None 表示该章
    **没有有效契约**(老章节/提取失败),用来验证"无契约章不算装置消失"。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="装置测试书", target_chapters=20,
                      target_words_per_chapter=3000, canon=canon)
    db.add(project)
    db.flush()
    for n, present in chapters:
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
        if present is None:
            continue  # 无契约章
        contract = dict(CONTRACT_BASE, devices_present=present)
        db.add(ChapterState(
            chapter_id=ch.id, contract=json.dumps(contract, ensure_ascii=False),
            content_hash=content_hash(text), extract_status="ok",
        ))
    db.commit()
    return db, project


def _by_name(states):
    return {s["name"]: s for s in states}


# ---------- device_states:断档统计 ----------
def test_device_states_counts_gap_since_last_appearance():
    from app.engines.devices import device_states

    # 系统:第1、2章出现,第3-5章消失 → 写第6章时 gap=3
    db, project = _make_db([(1, ["系统"]), (2, ["系统"]), (3, []), (4, []), (5, [])])
    st = _by_name(device_states(db, project.id, upto=6))
    assert st["系统"]["last_seen"] == 2
    assert st["系统"]["gap"] == 3
    assert st["系统"]["threshold"] == 2       # critical
    assert st["系统"]["overdue"] is True
    # 玉佩从未出现过 → 不算断档(可能后文才入手),不催不报
    assert st["那枚玉佩"]["last_seen"] is None
    assert st["那枚玉佩"]["gap"] is None
    assert st["那枚玉佩"]["overdue"] is False
    db.close()


def test_device_states_ignores_chapters_without_contract():
    from app.engines.devices import device_states

    # 第2-4章无契约(老书断档):不能当成"装置消失了3章"
    db, project = _make_db([(1, ["系统"]), (2, None), (3, None), (4, None)])
    st = _by_name(device_states(db, project.id, upto=5))
    assert st["系统"]["last_seen"] == 1
    assert st["系统"]["gap"] == 0       # 第1章之后没有任何**有契约**的章
    assert st["系统"]["overdue"] is False
    db.close()


def test_device_states_upto_is_exclusive_and_tolerant_matching():
    from app.engines.devices import device_states

    # 契约里写成「系统面板」/带书名号 → 仍应认作同一装置(宽容匹配)
    db, project = _make_db([(1, ["系统面板"]), (2, []), (3, ["《那枚玉佩》"])])
    st = _by_name(device_states(db, project.id, upto=4))
    assert st["系统"]["last_seen"] == 1
    assert st["那枚玉佩"]["last_seen"] == 3
    # upto 不含本章:写第 2 章时只看得见第 1 章
    st2 = _by_name(device_states(db, project.id, upto=2))
    assert st2["系统"]["gap"] == 0
    assert st2["那枚玉佩"]["last_seen"] is None
    db.close()


def test_device_states_empty_without_canon_devices():
    from app.engines.devices import device_states

    db, project = _make_db([(1, ["系统"])], canon=None)
    assert device_states(db, project.id, upto=2) == []
    db.close()


def test_device_states_threshold_by_importance():
    from app.engines.devices import device_states

    # minor 装置阈值 5:断档 3 章还不催(critical 同样断档 3 章已逾期)
    db, project = _make_db([
        (1, ["系统", "那枚玉佩"]), (2, []), (3, []), (4, []),
    ])
    st = _by_name(device_states(db, project.id, upto=5))
    assert st["系统"]["gap"] == 3 and st["系统"]["overdue"] is True
    assert st["那枚玉佩"]["gap"] == 3
    assert st["那枚玉佩"]["threshold"] == 5
    assert st["那枚玉佩"]["overdue"] is False
    db.close()


# ---------- 催场块 ----------
def test_reminder_block_nags_only_overdue():
    from app.engines.devices import devices_reminder_block

    db, project = _make_db([(1, ["系统", "那枚玉佩"]), (2, []), (3, [])])
    block = devices_reminder_block(db, project.id, 4)
    assert "常驻装置复现提醒" in block
    assert "系统" in block
    assert "上次露面在第1章" in block
    assert "已有 2 章没它的戏" in block
    assert "每章都应有存在感" in block   # cadence 带进催场文案
    assert "那枚玉佩" not in block        # minor 阈值 5,未逾期不唠叨
    db.close()


def test_reminder_block_empty_when_nothing_due():
    from app.engines.devices import devices_reminder_block

    # 刚出现过 → 空串(零 token,行为与旧版一致)
    db, project = _make_db([(1, ["系统"])])
    assert devices_reminder_block(db, project.id, 2) == ""
    # 无 canon 装置 → 空串
    db2, project2 = _make_db([(1, []), (2, []), (3, [])], canon=None)
    assert devices_reminder_block(db2, project2.id, 4) == ""
    # 装置从未出现过(设定在后文才登场)→ 不催
    db3, project3 = _make_db([(1, []), (2, []), (3, []), (4, [])])
    assert devices_reminder_block(db3, project3.id, 5) == ""
    db.close(); db2.close(); db3.close()


def test_roster_block_is_closed_set_for_extraction():
    from app.engines.devices import devices_roster_block

    db, project = _make_db([(1, [])])
    block = devices_roster_block(project)
    assert "只能从这份清单里认领" in block
    assert "- 系统" in block
    assert "- 那枚玉佩" in block
    # 无 canon → 空串(提取 prompt 里 devices_present 自然填 [])
    db2, project2 = _make_db([(1, [])], canon=None)
    assert devices_roster_block(project2) == ""
    assert devices_roster_block(None) == ""
    db.close(); db2.close()


# ---------- 软报:催过了还不出现才报 ----------
def test_check_device_gaps_fires_one_chapter_beyond_reminder():
    from app.engines.devices import check_device_gaps, device_states

    # critical 阈值 2:gap=2 只催不报;gap=3(催过了本章还是没出现)才软报
    db, project = _make_db([(1, ["系统"]), (2, []), (3, [])])
    assert check_device_gaps(device_states(db, project.id, upto=4), 3) == []

    db2, project2 = _make_db([(1, ["系统"]), (2, []), (3, []), (4, [])])
    issues = check_device_gaps(device_states(db2, project2.id, upto=5), 4)
    assert len(issues) == 1
    it = issues[0]
    assert it["severity"] == "major"        # critical 装置 → major
    assert it["type"] == "worldrule"
    assert "系统" in it["description"]
    assert "连续 3 章" in it["description"]
    assert "上次露面在第1章" in it["description"]
    assert it["suggestion"]
    db.close(); db2.close()


def test_check_device_gaps_severity_and_never_blocker():
    from app.engines.devices import check_device_gaps, device_states

    # minor 装置阈值 5 → gap 需达 6 才报,且 severity 只到 minor
    chapters = [(1, ["那枚玉佩"])] + [(n, []) for n in range(2, 8)]
    db, project = _make_db(chapters)
    issues = check_device_gaps(device_states(db, project.id, upto=8), 7)
    minor = [i for i in issues if "那枚玉佩" in i["description"]]
    assert minor and minor[0]["severity"] == "minor"
    assert all(i["severity"] != "blocker" for i in issues)  # advisory 绝不阻断
    db.close()


def test_persist_device_issues_and_canon_untouched():
    from app.db.models import Chapter, ChapterIssue, Project
    from app.engines.devices import persist_device_issues

    db, project = _make_db([(1, ["系统"]), (2, []), (3, []), (4, [])])
    ch4 = db.query(Chapter).filter(Chapter.chapter_number == 4).first()
    persist_device_issues(db, project.id, ch4, ch4.final_content)

    rows = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == ch4.id, ChapterIssue.source == "devices"
    ).all()
    assert len(rows) == 1
    assert "系统" in rows[0].description
    assert rows[0].severity == "major" and rows[0].status == "open"
    assert rows[0].issue_type == "worldrule"
    # advisory 不动宪法(唯一 canon 写路径是作者触发的 adopt-canon)
    assert db.get(Project, project.id).canon == CANON
    db.close()


def test_persist_device_issues_clears_when_device_returns():
    """装置补回来后重跑:旧告警幂等清掉(persist_issues 按 source purge 重建)。"""
    from app.db.models import Chapter, ChapterIssue, ChapterState
    from app.engines.devices import persist_device_issues
    from app.engines.editorial import content_hash

    db, project = _make_db([(1, ["系统"]), (2, []), (3, []), (4, [])])
    ch4 = db.query(Chapter).filter(Chapter.chapter_number == 4).first()
    persist_device_issues(db, project.id, ch4, ch4.final_content)
    assert db.query(ChapterIssue).filter(ChapterIssue.source == "devices").count() == 1

    # 第 4 章重写后系统回来了 → 契约更新,重跑校验,告警应清空
    row = db.query(ChapterState).filter(ChapterState.chapter_id == ch4.id).first()
    fixed = dict(json.loads(row.contract), devices_present=["系统"])
    row.contract = json.dumps(fixed, ensure_ascii=False)
    row.content_hash = content_hash(ch4.final_content)
    db.commit()
    persist_device_issues(db, project.id, ch4, ch4.final_content)
    assert db.query(ChapterIssue).filter(ChapterIssue.source == "devices").count() == 0
    db.close()


# ---------- 契约字段:归一 + 提取 prompt 带闭集清单 ----------
def test_validate_contract_normalizes_devices_present():
    from app.engines.pipeline.handoff import validate_contract

    c = validate_contract(dict(CONTRACT_BASE, devices_present=["系统", " 系统 ", "", None, "玉佩"]))
    assert c["devices_present"] == ["系统", "玉佩"]   # 去空白 + 去重 + 丢空值
    # 非 list / 缺键 → 空列表(老契约向后兼容)
    assert validate_contract(dict(CONTRACT_BASE, devices_present="系统"))["devices_present"] == []
    assert validate_contract(dict(CONTRACT_BASE))["devices_present"] == []
    # devices_present 不参与"三项核心全空"判定(单有装置不足以成契约)
    assert validate_contract({"devices_present": ["系统"]}) is None


def test_extract_prompt_carries_device_roster():
    from app.db.models import Chapter
    from app.engines.pipeline.handoff import extract_handoff_contract

    class _Adapter:
        def __init__(self):
            self.prompts: list[str] = []

        async def ask(self, prompt: str, system=None) -> str:
            self.prompts.append(prompt)
            return json.dumps(dict(CONTRACT_BASE, devices_present=["系统"]),
                              ensure_ascii=False)

    db, project = _make_db([(1, None)])
    ch1 = db.query(Chapter).filter(Chapter.chapter_number == 1).first()
    adapter = _Adapter()
    asyncio.run(extract_handoff_contract(db, ch1, 1, ch1.final_content, adapter))

    prompt = adapter.prompts[0]
    assert "本书登记的常驻装置" in prompt
    assert "- 系统" in prompt
    # 落库的契约带上了装置出场记录
    from app.db.models import ChapterState

    row = db.query(ChapterState).filter(ChapterState.chapter_id == ch1.id).first()
    assert row.extract_status == "ok"
    assert json.loads(row.contract)["devices_present"] == ["系统"]
    db.close()


def test_extract_prompt_has_no_roster_without_canon():
    from app.db.models import Chapter
    from app.engines.pipeline.handoff import extract_handoff_contract

    class _Adapter:
        def __init__(self):
            self.prompts: list[str] = []

        async def ask(self, prompt: str, system=None) -> str:
            self.prompts.append(prompt)
            return json.dumps(CONTRACT_BASE, ensure_ascii=False)

    db, project = _make_db([(1, None)], canon=None)
    ch1 = db.query(Chapter).filter(Chapter.chapter_number == 1).first()
    adapter = _Adapter()
    asyncio.run(extract_handoff_contract(db, ch1, 1, ch1.final_content, adapter))
    assert "本书登记的常驻装置" not in adapter.prompts[0]
    db.close()
