# tests/test_resource_ledger.py
# -*- coding: utf-8 -*-
"""角色资源账本(P2-7):possession/ability 从硬约束里分流、闭集红线、退场过滤、
超限截断,以及 replaces 的宽容匹配与「未命中不静默」。

这条线治的是长篇的老毛病:第 12 章凭空掏出一把从没提过的匕首、第 3 章送出去的玉佩
第 20 章又戴回来、干粮吃完三章账本里还挂着「持有半块干粮」。
"""
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _project(client: TestClient, headers: dict, title: str = "账本书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _change(entity: str, fact_type: str, content: str, **kw) -> dict:
    return {
        "entity": entity,
        "fact_type": fact_type,
        "content": content,
        "other_entity": None,
        "importance": kw.get("importance", "major"),
        "replaces": kw.get("replaces"),
    }


def _bible(pid: int):
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    db = SessionLocal()
    return db, BibleService(db, pid)


def test_resources_split_out_of_hard_constraints(client):
    """持有/能力只进账本,状态只进硬约束——同一条不许在 prompt 里出现两遍。"""
    from app.engines.consistency import RESOURCE_FACT_TYPES, ledger_block

    headers = _auth(client, "ledger_split")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(2, {"fact_changes": [
            _change("郭靖", "state", "左臂骨裂"),
            _change("郭靖", "possession", "持有半块干粮"),
            _change("郭靖", "ability", "会降龙十八掌", importance="critical"),
        ]})
        db.commit()

        hard = bible.hard_constraints_block(
            3, ["郭靖"], exclude_types=RESOURCE_FACT_TYPES
        )
        assert "左臂骨裂" in hard
        assert "半块干粮" not in hard and "降龙十八掌" not in hard

        block = ledger_block(bible, 3, ["郭靖"])
        assert "半块干粮" in block and "降龙十八掌" in block
        assert "左臂骨裂" not in block
        # 不传 exclude_types 时保持老行为(章后抽取的对照清单要看到资源,
        # 否则模型抄不到「持有…」那一行,replaces 永远填不对)
        assert "半块干粮" in bible.hard_constraints_block(3, ["郭靖"])
    finally:
        db.close()


def test_empty_ledger_is_blank_not_a_red_line(client):
    """账本为空 → 空串:开篇几章人物本来就在不停拿到新东西,此时红线只会压制正常叙事。"""
    from app.engines.consistency import ledger_block

    headers = _auth(client, "ledger_empty")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(1, {"fact_changes": [_change("郭靖", "state", "初入江湖")]})
        db.commit()
        assert ledger_block(bible, 2) == ""
    finally:
        db.close()


def test_ledger_red_lines_and_labels(client):
    """三条红线都在,且能力/持有分别标签、critical 标 ❗、蓝图关键道具留了活口。"""
    from app.engines.consistency import ledger_block

    headers = _auth(client, "ledger_rules")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(2, {"fact_changes": [
            _change("郭靖", "possession", "持有九阴真经抄本", importance="critical"),
            _change("郭靖", "ability", "会蛤蟆功", importance="minor"),
        ]})
        db.commit()
        block = ledger_block(bible, 4, ["郭靖"])
        assert "❗ 郭靖 持有:持有九阴真经抄本(自第2章起)" in block
        assert "· 郭靖 会/能:会蛤蟆功(自第2章起)" in block
        assert "不许凭空掏出" in block
        assert "交代来源" in block
        assert "已经失去的东西,后文不许再拿出来用" in block
        # 留活口:本章蓝图点名的关键道具不算凭空,否则正常的「本章拿到新东西」会被压死
        assert "本章蓝图【关键道具】点名的不算凭空" in block
    finally:
        db.close()


def test_retired_entity_resources_not_injected(client):
    """人退场了,他手上有什么不再约束后续生成(与硬约束同一口径)。"""
    from app.db.models import Entity
    from app.engines.consistency import ledger_block

    headers = _auth(client, "ledger_retired")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(2, {"fact_changes": [
            _change("黄药师", "possession", "持有碧海潮生曲谱"),
        ]})
        db.commit()
        assert "碧海潮生曲谱" in ledger_block(bible, 3)

        ent = db.query(Entity).filter(
            Entity.project_id == p["id"], Entity.name == "黄药师"
        ).first()
        ent.retired = True
        db.commit()
        assert ledger_block(bible, 3) == ""
    finally:
        db.close()


def test_ledger_caps_lines_keeping_critical(client):
    """超上限按重要度截断,critical 不被砍——资源多的长篇不许把 prompt 撑爆。"""
    from app.engines.consistency.ledger import _MAX_RESOURCE_LINES, resource_facts

    headers = _auth(client, "ledger_cap")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        changes = [
            _change("郭靖", "possession", f"持有杂物{i}", importance="minor")
            for i in range(_MAX_RESOURCE_LINES + 6)
        ]
        changes.append(_change("郭靖", "possession", "持有师父的遗信", importance="critical"))
        bible.apply_extraction(2, {"fact_changes": changes})
        db.commit()
        facts = resource_facts(bible, 3, ["郭靖"])
        assert len(facts) == _MAX_RESOURCE_LINES
        assert any(f.content == "持有师父的遗信" for f in facts)
    finally:
        db.close()


def test_replaces_tolerates_wording_drift(client):
    """replaces 措辞有出入也要收口:原先只做精确相等,模型多写个括号补注就永远关不掉。"""
    from app.engines.consistency import ledger_block

    headers = _auth(client, "ledger_drift")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(2, {"fact_changes": [
            _change("郭靖", "possession", "持有半块干粮"),
        ]})
        db.commit()
        stats = bible.apply_extraction(5, {"fact_changes": [
            _change("郭靖", "possession", "干粮已吃完",
                    replaces="持有半块干粮(从张三处换得)"),
        ]})
        db.commit()
        assert stats["closed"] == 1
        block = ledger_block(bible, 5, ["郭靖"])
        assert "干粮已吃完" in block
        assert "持有半块干粮" not in block  # 旧事实已收口,不再当硬约束注入
    finally:
        db.close()


def test_replaces_miss_is_logged_not_silent(client, caplog):
    """replaces 没命中任何有效事实 → 必须留 warning:静默无操作正是账本失真的来源。"""
    headers = _auth(client, "ledger_miss")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        with caplog.at_level(logging.WARNING, logger="jarvis-write.bible"):
            stats = bible.apply_extraction(4, {"fact_changes": [
                _change("郭靖", "possession", "玉佩已送人", replaces="持有一块从未登记的玉佩"),
            ]})
            db.commit()
        assert stats["closed"] == 0
        assert "replaces 未命中" in caplog.text
        assert "持有一块从未登记的玉佩" in caplog.text
    finally:
        db.close()


def test_replaces_ambiguous_is_not_guessed(client):
    """一条 replaces 模糊命中多行 → 歧义不猜、不乱收口(宁可漏配也不误配)。"""
    headers = _auth(client, "ledger_ambiguous")
    p = _project(client, headers)
    db, bible = _bible(p["id"])
    try:
        bible.apply_extraction(2, {"fact_changes": [
            _change("郭靖", "possession", "持有玉佩甲一枚"),
            _change("郭靖", "possession", "持有玉佩乙一枚"),
        ]})
        db.commit()
        stats = bible.apply_extraction(6, {"fact_changes": [
            _change("郭靖", "possession", "玉佩已丢", replaces="持有玉佩"),
        ]})
        db.commit()
        assert stats["closed"] == 0
    finally:
        db.close()


def test_prompts_carry_the_ledger_placeholder():
    """三处 prompt(草稿/定稿/门禁)都得留账本位:少一处,那一步就看不到账本。"""
    from app.prompts.chapter import CHAPTER_DRAFT_PROMPT, CHAPTER_FINALIZE_PROMPT
    from app.prompts.consistency import CONSISTENCY_CHECK_PROMPT

    for tpl in (CHAPTER_DRAFT_PROMPT, CHAPTER_FINALIZE_PROMPT, CONSISTENCY_CHECK_PROMPT):
        assert "{resource_ledger}" in tpl
