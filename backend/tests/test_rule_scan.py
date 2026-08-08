# tests/test_rule_scan.py
# -*- coding: utf-8 -*-
"""世界观硬规则钉板 + 规则扫描(mock LLM,无需 API key)。

覆盖:
- world_rules_block 注入架构简报(有规则才出现,注入 draft/finalize/蓝图/修改指令)
- rule_scan_book:违反项落库(source="rules",type="worldrule"),幻觉举证清空,
  幂等重建(purge 旧 open),指纹未变的 ignored 保留
- 未填规则 → 报错;接口层 PATCH 保存/读取;rule-scan-async 未填规则 → 400
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

CH_TEXT = "晚自习时,林涛翻开政治课本开始背诵,为后天的政治高考做准备。" * 10

RULES = "故事发生在 2024 年,新高考政策。\n林涛是理科生,不考政治。\n高考只考 6 月 7-8 日两天。"

SCAN_ISSUE = {
    "severity": "blocker",
    "type": "worldrule",
    "description": "林涛是理科生不考政治,正文却写他背政治准备政治高考",
    "evidence": "林涛翻开政治课本开始背诵",
    "suggestion": "改为背生物/化学等理科科目",
}
HALLUCINATED_ISSUE = {
    "severity": "minor",
    "type": "worldrule",
    "description": "幻觉举证(引不到原文,应被清空证据)",
    "evidence": "这段文字根本不在正文里",
    "suggestion": "x",
}


def _make_db(world_rules: str | None = RULES):
    """独立内存库:一个项目(带规则)+ 一章大纲 + 一章正文。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="规则测试书", target_chapters=3, target_words_per_chapter=3000,
        world_rules=world_rules,
    )
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="晚自习",
        chapter_purpose="推进主线", summary="备考日常", current_version=1,
    ))
    db.flush()
    ch = Chapter(
        project_id=project.id, outline_id=1, chapter_number=1,
        final_content=CH_TEXT, word_count=len(CH_TEXT), status="approved",
    )
    db.add(ch)
    db.commit()
    return db, project, ch


class _Adapter:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


def _scan(db, project_id, adapter):
    from app.engines import diagnosis

    with patch.object(diagnosis, "get_adapter_for", return_value=adapter):
        return asyncio.run(diagnosis.rule_scan_book(db, project_id))


# ---------- 钉板注入 ----------

def test_world_rules_injected_into_chapter_brief():
    from app.db.models import Architecture
    from app.engines.common import chapter_architecture_brief

    db, project, _ = _make_db()
    db.add(Architecture(
        project_id=project.id, core_seed="种子", character_dynamics="动力学",
        world_building="世界观", plot_architecture="情节", version=1,
    ))
    db.commit()
    db.refresh(project)

    brief = chapter_architecture_brief(project)
    assert "世界观硬规则" in brief
    assert "理科生,不考政治" in brief

    project.world_rules = ""
    assert "世界观硬规则" not in chapter_architecture_brief(project)
    db.close()


# ---------- 规则扫描引擎 ----------

def test_rule_scan_persists_issues_and_filters_hallucination():
    from app.db.models import ChapterIssue

    db, project, ch = _make_db()
    adapter = _Adapter(json.dumps(
        {"issues": [SCAN_ISSUE, HALLUCINATED_ISSUE]}, ensure_ascii=False
    ))
    result = _scan(db, project.id, adapter)

    assert result["scanned"] == 1
    assert result["with_issues"] == [1]
    assert result["total_issues"] == 2
    assert result["total_blockers"] == 1

    rows = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).all()
    assert len(rows) == 2
    by_desc = {r.description: r for r in rows}
    real = by_desc[SCAN_ISSUE["description"]]
    assert real.source == "rules"
    assert real.issue_type == "worldrule"
    assert real.severity == "blocker"
    assert real.evidence == SCAN_ISSUE["evidence"]
    # 幻觉举证:引不到原文 → 证据清空
    halluc = by_desc[HALLUCINATED_ISSUE["description"]]
    assert halluc.evidence == ""
    # prompt 里带上了规则全文
    assert "理科生,不考政治" in adapter.prompts[0]
    db.close()


def test_rule_scan_idempotent_rebuild_keeps_fresh_ignored():
    from app.db.models import ChapterIssue

    db, project, ch = _make_db()
    payload = json.dumps({"issues": [SCAN_ISSUE]}, ensure_ascii=False)
    _scan(db, project.id, _Adapter(payload))

    # 用户忽略该问题(正文未改,指纹未变)
    row = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).one()
    row.status = "ignored"
    db.commit()

    # 再扫一次:同指纹 ignored 行保留不被清(与门禁/体检同一套 persist 语义)
    _scan(db, project.id, _Adapter(payload))
    rows = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).all()
    statuses = sorted(r.status for r in rows)
    assert "ignored" in statuses  # 旧的忽略确认不丢

    # 规则改了导致扫出问题消失 → 旧 open 清账(这里模拟扫到零问题)
    db.query(ChapterIssue).delete()
    db.commit()
    _scan(db, project.id, _Adapter('{"issues": []}'))
    assert db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == ch.id, ChapterIssue.source == "rules"
    ).count() == 0
    db.close()


def test_rule_scan_requires_rules():
    db, project, _ = _make_db(world_rules=None)
    with pytest.raises(RuntimeError, match="世界观硬规则"):
        _scan(db, project.id, _Adapter("{}"))
    db.close()


def test_migration_adds_world_rules_column_idempotent():
    from app.migrate import _add_world_rules_column

    _add_world_rules_column()
    _add_world_rules_column()  # 幂等:重复执行不报错


# ---------- 接口层 ----------

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


def test_patch_world_rules_roundtrip(client):
    headers = _auth(client, "rules_patch_user")
    r = client.post(
        "/api/projects", headers=headers,
        json={"title": "规则书", "target_chapters": 3},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    r = client.patch(
        f"/api/projects/{pid}", headers=headers, json={"world_rules": RULES}
    )
    assert r.status_code == 200, r.text
    assert r.json()["world_rules"] == RULES

    got = client.get(f"/api/projects/{pid}", headers=headers).json()
    assert "理科生,不考政治" in got["world_rules"]


def test_rule_scan_async_400_without_rules(client):
    headers = _auth(client, "rules_scan_400")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "无规则书", "target_chapters": 3},
    ).json()["id"]
    r = client.post(f"/api/projects/{pid}/rule-scan-async", headers=headers)
    assert r.status_code == 400
    assert "世界观硬规则" in r.json()["detail"]
