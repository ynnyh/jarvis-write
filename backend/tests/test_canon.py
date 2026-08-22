# tests/test_canon.py
# -*- coding: utf-8 -*-
"""故事宪法(Canon):schema coerce + constitution_block 合并注入 + 门禁读取 +
迁移幂等 + 接口 PATCH 回环(mock LLM,无需 API key)。

覆盖长程一致性重构 Phase 1 的结构核心:
- coerce_canon:LLM dict / 存量 None / 脏数据收敛(absences 单条也吃、devices 丢无名、
  deadline 需 name、total_days "31"→31、未知 importance 回落)
- StoryCanon.render / is_empty
- constitution_block:world_rules + 结构化 canon 合并;canon 为空时与旧 world_rules_block
  逐字相同(向后兼容,老项目行为不变);注入 chapter_architecture_brief
- check_chapter:本书宪法作为一等对照源进了门禁 prompt(此前门禁拿不到 world_rules/canon)
- 迁移 _add_canon_column 幂等;PATCH /api/projects 保存/读取 canon 回环
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest


# ---------- coerce_canon:脏输入收敛 ----------

def test_coerce_canon_empty_and_none():
    from app.schemas.canon import coerce_canon

    assert coerce_canon(None).is_empty()
    assert coerce_canon("garbage").is_empty()
    assert coerce_canon({}).is_empty()
    assert coerce_canon([]).is_empty()  # 非 dict


def test_coerce_canon_fields_and_cleaning():
    from app.schemas.canon import coerce_canon

    c = coerce_canon({
        "absences": ["大院只有主仆女三人,无其他仆役", "   ", "女主没有家人"],
        "devices": [
            {"name": "系统", "cadence": "每章都应有存在感", "importance": "critical"},
            {"name": "   ", "cadence": "x"},   # 无名 → 丢
            {"cadence": "无 name"},             # 无 name → 丢
            "not a dict",                       # 非 dict → 丢
        ],
        "deadline": {"name": "任务倒计时", "total_days": "31", "anchor_chapter": 2},
        "unknown_key": "应被丢弃",
    })
    assert c.absences == ["大院只有主仆女三人,无其他仆役", "女主没有家人"]
    assert len(c.devices) == 1
    assert c.devices[0].name == "系统"
    assert c.devices[0].importance == "critical"
    assert c.deadline is not None
    assert c.deadline.total_days == 31          # "31" → 31
    assert c.deadline.anchor_chapter == 2
    assert c.deadline.importance == "critical"  # deadline 默认 critical
    assert not c.is_empty()


def test_coerce_canon_absences_str_and_bad_importance():
    from app.schemas.canon import coerce_canon

    c = coerce_canon({
        "absences": "单条字符串也吃",
        "devices": [{"name": "读心术", "importance": "谁知道呢"}],
    })
    assert c.absences == ["单条字符串也吃"]
    assert c.devices[0].importance == "major"  # 未知 importance → 默认 major


def test_coerce_canon_deadline_needs_name():
    from app.schemas.canon import coerce_canon

    # 无 name 的 deadline 不成立
    assert coerce_canon({"deadline": {"total_days": 31}}).deadline is None
    # 脏 total_days 回落 0(仍保留 deadline,因为有 name)
    c = coerce_canon({"deadline": {"name": "大婚之期", "total_days": "soon"}})
    assert c.deadline is not None
    assert c.deadline.total_days == 0
    assert c.deadline.anchor_chapter == 1  # 缺省起算章


def test_canon_render_contains_all_three_and_empty_blank():
    from app.schemas.canon import coerce_canon

    text = coerce_canon({
        "absences": ["大院无仆役"],
        "devices": [{"name": "系统"}],
        "deadline": {"name": "任务倒计时", "total_days": 31, "anchor_chapter": 1},
    }).render()
    assert "大院无仆役" in text
    assert "系统" in text
    assert "31" in text and "倒计时" in text
    # 空 canon 渲染空串(调用方据此决定是否出整块)
    assert coerce_canon(None).render() == ""


# ---------- constitution_block:合并宪法块 + 向后兼容 ----------

def _project(world_rules=None, canon=None):
    from app.db.models import Project

    return Project(
        title="宪法测试书", target_chapters=3,
        world_rules=world_rules, canon=canon,
    )


def test_constitution_block_backward_compat_world_rules_only():
    from app.engines.common import constitution_block, world_rules_block

    p = _project(world_rules="林涛是理科生,不考政治")
    # 无 canon:与旧 world_rules_block 逐字相同(向后兼容,不改既有生成行为)
    assert constitution_block(p) == world_rules_block(p)
    assert "理科生,不考政治" in constitution_block(p)


def test_constitution_block_empty_returns_blank():
    from app.engines.common import constitution_block

    assert constitution_block(_project()) == ""


def test_constitution_block_merges_world_rules_and_canon():
    from app.engines.common import constitution_block

    p = _project(
        world_rules="高考只考两天",
        canon={"absences": ["大院无仆役"], "devices": [{"name": "系统"}]},
    )
    block = constitution_block(p)
    assert "高考只考两天" in block   # world_rules 段仍在
    assert "大院无仆役" in block      # canon 留白
    assert "系统" in block            # canon 装置


def test_constitution_block_canon_only():
    from app.engines.common import constitution_block

    p = _project(canon={"deadline": {"name": "任务倒计时", "total_days": 31}})
    block = constitution_block(p)
    assert "倒计时" in block and "31" in block


def test_canon_injected_into_chapter_brief():
    from app.engines.common import chapter_architecture_brief

    # architecture 为 None 时 base="(无)",宪法块仍应拼在尾部
    p = _project(canon={"absences": ["大院里只有三人,没有仆役"]})
    assert "大院里只有三人,没有仆役" in chapter_architecture_brief(p)


# ---------- 门禁读取:本书宪法进了 check_chapter 的对照 prompt ----------

class _Adapter:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


def _make_db_with_canon(canon):
    """内存库:一个带 canon 的项目 + 第 1 章(有正文,供第 2 章门禁拿到对照源)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="宪法测试书", target_chapters=5, target_words_per_chapter=3000,
        canon=canon,
    )
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="开篇",
        chapter_purpose="立设定", summary="大院日常", current_version=1,
    ))
    db.flush()
    db.add(Chapter(
        project_id=project.id, outline_id=1, chapter_number=1,
        final_content="第一章正文。" * 60, word_count=420, status="approved",
    ))
    db.commit()
    return db, project


def test_check_chapter_prompt_carries_canon():
    from app.engines.consistency import checker

    absence = "大院里只有主人、保镖、女主三人,没有其他仆役"
    db, project = _make_db_with_canon({"absences": [absence]})
    adapter = _Adapter(json.dumps({"issues": []}, ensure_ascii=False))
    with patch.object(checker, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(
            checker.check_chapter(db, project.id, 2, "第二章正文。" * 60)
        )
    assert issues == []
    assert adapter.prompts, "第 2 章有上一章正文,门禁应真的调用 LLM(未被早退)"
    # 本书宪法(刻意留白)进了门禁对照 prompt——此前门禁根本拿不到 world_rules/canon
    assert absence in adapter.prompts[0]
    db.close()


def test_check_chapter_no_canon_still_works():
    from app.engines.consistency import checker

    # 无 canon 的项目:门禁照常跑,constitution 段落为占位提示,不报错
    db, project = _make_db_with_canon(None)
    adapter = _Adapter(json.dumps({"issues": []}, ensure_ascii=False))
    with patch.object(checker, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(
            checker.check_chapter(db, project.id, 2, "第二章正文。" * 60)
        )
    assert issues == []
    assert adapter.prompts
    assert "本书未设定额外世界观硬规则/故事宪法" in adapter.prompts[0]
    db.close()


# ---------- 迁移幂等 ----------

def test_migration_adds_canon_column_idempotent():
    from app.migrate import _add_canon_column

    _add_canon_column()
    _add_canon_column()  # 幂等:重复执行不报错


# ---------- 接口层:PATCH 保存/读取 canon 回环 ----------

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth(client, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_patch_canon_roundtrip(client):
    headers = _auth(client, "canon_patch_user")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "宪法书", "target_chapters": 3},
    ).json()["id"]

    canon = {
        "absences": ["大院只有三人,无仆役"],
        "devices": [{"name": "系统", "cadence": "每章有存在感", "importance": "critical"}],
        "deadline": {"name": "任务倒计时", "total_days": 31, "anchor_chapter": 1},
    }
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"canon": canon})
    assert r.status_code == 200, r.text
    got = r.json()["canon"]
    assert got["absences"] == ["大院只有三人,无仆役"]
    assert got["devices"][0]["name"] == "系统"
    assert got["deadline"]["total_days"] == 31

    # GET 再读一遍,落库/读回一致
    got2 = client.get(f"/api/projects/{pid}", headers=headers).json()["canon"]
    assert got2["deadline"]["name"] == "任务倒计时"
    assert got2["devices"][0]["importance"] == "critical"


# ---------- Phase 1c:LLM 提议宪法(咨询式,不自动落库 + 一键采纳)----------

def test_build_canon_suggestion_issues_shapes_and_dedup():
    from app.engines.consistency.extractor import _build_canon_suggestion_issues
    from app.schemas.canon import coerce_canon

    existing = coerce_canon({"absences": ["大院只有三人,无仆役"]})
    issues = _build_canon_suggestion_issues([
        {"kind": "absence", "text": "大院只有三人,无仆役", "evidence": "e", "reason": "r"},  # 已在宪法→丢
        {"kind": "absence", "text": "女主没有家人在世", "evidence": "e2", "reason": "r2"},
        {"kind": "device", "name": "系统", "cadence": "每章有存在感", "importance": "谁知道", "evidence": "e3"},
        {"kind": "deadline", "name": "任务倒计时", "total_days": "31", "anchor_chapter": 2, "evidence": "e4"},
        "不是 dict",                       # 坏形状→跳过
        {"kind": "device", "name": "   "},  # 无名→跳过
        {"kind": "deadline", "name": "第二个倒计时"},  # 同批已有 deadline→丢
    ], existing)

    by_type = {i["type"]: i for i in issues}
    assert set(by_type) == {"absence", "device", "deadline"}  # 去重后各一条
    assert by_type["absence"]["payload"]["text"] == "女主没有家人在世"
    assert by_type["device"]["payload"]["importance"] == "major"  # 未知重要度→回落 major
    assert by_type["deadline"]["payload"]["total_days"] == 31     # "31"→31
    assert by_type["deadline"]["payload"]["anchor_chapter"] == 2
    # 全部是不阻断的建议
    assert all(i["severity"] == "minor" for i in issues)


def test_build_canon_suggestion_issues_caps_at_three():
    from app.engines.consistency.extractor import _build_canon_suggestion_issues
    from app.schemas.canon import StoryCanon

    many = [{"kind": "absence", "text": f"留白{i}"} for i in range(6)]
    assert len(_build_canon_suggestion_issues(many, StoryCanon())) == 3  # 最多 3 条


def test_adopt_into_canon_merges_and_idempotent():
    from app.api.chapters.issues import _adopt_into_canon

    # 空 canon 采纳装置 → 加入
    c1, changed1 = _adopt_into_canon(None, {"kind": "device", "name": "系统", "importance": "critical"})
    assert changed1 and c1["devices"][0]["name"] == "系统"
    # 再采纳同名装置 → 幂等不重复
    c2, changed2 = _adopt_into_canon(c1, {"kind": "device", "name": "系统"})
    assert changed2 is False and len(c2["devices"]) == 1
    # 采纳留白
    c3, changed3 = _adopt_into_canon(c2, {"kind": "absence", "text": "大院无仆役"})
    assert changed3 and "大院无仆役" in c3["absences"]
    # 采纳倒计时
    c4, changed4 = _adopt_into_canon(c3, {"kind": "deadline", "name": "任务倒计时", "total_days": 31})
    assert changed4 and c4["deadline"]["total_days"] == 31
    # 已有倒计时不覆盖
    c5, changed5 = _adopt_into_canon(c4, {"kind": "deadline", "name": "别的倒计时", "total_days": 99})
    assert changed5 is False and c5["deadline"]["name"] == "任务倒计时"


def _run_extract_with_suggestions(canon, suggestions):
    """在带 canon 的内存库上跑一次 extract_and_apply(mock LLM 只回 canon_suggestions),
    返回 (db, project, stats)。核心圣经抽取为空,专验 canon 建议通道。"""
    from app.engines.consistency import extractor

    db, project = _make_db_with_canon(canon)
    extraction = {
        "new_entities": [], "fact_changes": [],
        "foreshadow_ops": [], "knowledge_updates": [],
        "canon_suggestions": suggestions,
    }
    adapter = _Adapter(json.dumps(extraction, ensure_ascii=False))
    with patch.object(extractor, "get_adapter_for", return_value=adapter):
        stats = asyncio.run(
            extractor.extract_and_apply(db, project.id, 1, "第一章正文。" * 60)
        )
    return db, project, stats


def test_extract_persists_canon_suggestions_without_autowrite():
    """核心断言(方案边界):LLM 建议落成 source=canon 的 advisory issue,
    但【绝不自动写 project.canon】——留白/缺席检测不可靠,必须人工确认。"""
    from app.db.models import ChapterIssue, Project

    db, project, stats = _run_extract_with_suggestions(None, [
        {"kind": "absence", "text": "大院里只有三人,没有仆役", "evidence": "院中空荡荡", "reason": "闭集留白"},
        {"kind": "device", "name": "系统", "cadence": "每章都应有存在感", "importance": "critical", "evidence": "系统提示音响起"},
        {"kind": "deadline", "name": "任务倒计时", "total_days": 31, "anchor_chapter": 1, "evidence": "还有三十一天"},
    ])

    assert stats.get("canon_suggestions") == 3
    rows = db.query(ChapterIssue).filter(ChapterIssue.source == "canon").all()
    assert len(rows) == 3
    by_type = {r.issue_type: r for r in rows}
    assert set(by_type) == {"absence", "device", "deadline"}
    assert by_type["device"].payload["name"] == "系统"
    assert by_type["deadline"].payload["total_days"] == 31
    assert all(r.status == "open" and r.severity == "minor" for r in rows)

    # 关键:project.canon 仍为空,LLM 绝不自动落库
    fresh = db.get(Project, project.id)
    assert fresh.canon is None
    db.close()


def test_extract_canon_suggestion_dedups_existing_canon():
    """已在 canon 里的建议不再冒出(采纳后自然不复现,不靠 issue 状态去重)。"""
    from app.db.models import ChapterIssue

    db, _project, _stats = _run_extract_with_suggestions(
        {"absences": ["大院里只有三人,没有仆役"]},
        [
            {"kind": "absence", "text": "大院里只有三人,没有仆役", "evidence": "e", "reason": "r"},  # 已有→丢
            {"kind": "absence", "text": "女主没有家人在世", "evidence": "e2", "reason": "r2"},         # 新
        ],
    )
    rows = db.query(ChapterIssue).filter(ChapterIssue.source == "canon").all()
    assert len(rows) == 1
    assert rows[0].payload["text"] == "女主没有家人在世"
    db.close()


def test_extract_no_canon_suggestions_is_noop():
    """抽取无 canon 建议时不落库、不报错(stats 无 canon_suggestions 键)。"""
    from app.db.models import ChapterIssue

    db, _project, stats = _run_extract_with_suggestions(None, [])
    assert "canon_suggestions" not in stats
    assert db.query(ChapterIssue).filter(ChapterIssue.source == "canon").count() == 0
    db.close()


def test_migration_adds_issue_payload_column_idempotent():
    from app.migrate import _add_issue_payload_column

    _add_issue_payload_column()
    _add_issue_payload_column()  # 幂等:重复执行不报错


def test_adopt_canon_suggestion_via_api(client):
    """HTTP 采纳:建议 issue → project.canon 落库 + issue 标 resolved;二次采纳报 400。"""
    from app.db.models import Chapter, ChapterIssue, Outline
    from app.db.session import SessionLocal

    headers = _auth(client, "canon_adopt_user")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "采纳书", "target_chapters": 3},
    ).json()["id"]

    # 直接落一章 + 一条 canon 建议(建章走生成要 LLM,太重;绕过接口直插库)
    s = SessionLocal()
    try:
        s.add(Outline(
            project_id=pid, chapter_number=1, title="开篇",
            chapter_purpose="立设定", summary="日常", current_version=1,
        ))
        s.flush()
        ch = Chapter(
            project_id=pid, outline_id=1, chapter_number=1,
            final_content="正文", word_count=2, status="approved",
        )
        s.add(ch)
        s.flush()
        issue = ChapterIssue(
            chapter_id=ch.id, source="canon", severity="minor", issue_type="device",
            description="建议加入常驻装置:系统", evidence="系统提示音响起",
            suggestion="金手指", status="open", content_hash="x",
            payload={"kind": "device", "name": "系统", "cadence": "每章有存在感", "importance": "critical"},
        )
        s.add(issue)
        s.commit()
        issue_id = issue.id
    finally:
        s.close()

    base = f"/api/projects/{pid}/chapters/1/issues/{issue_id}"
    r = client.post(f"{base}/adopt-canon", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["canon"]["devices"][0]["name"] == "系统"
    assert body["issue"]["status"] == "resolved"

    # canon 已落库(GET 再读一遍)
    got = client.get(f"/api/projects/{pid}", headers=headers).json()["canon"]
    assert got["devices"][0]["importance"] == "critical"

    # 二次采纳:issue 已 resolved → 400
    r2 = client.post(f"{base}/adopt-canon", headers=headers)
    assert r2.status_code == 400
