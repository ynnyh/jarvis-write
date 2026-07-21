# tests/test_relationships.py
# -*- coding: utf-8 -*-
"""人物关系闭环:抽取双写(facts + relationships)、时序更新、
生成注入(只注入本章出场对)、人物卡 relations 展示、归属 404。"""
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


def _create_project(client: TestClient, headers: dict, title: str = "关系书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _rel_change(entity: str, other: str, content: str) -> dict:
    return {
        "entity": entity,
        "fact_type": "relationship",
        "content": content,
        "other_entity": other,
        "importance": "major",
        "replaces": None,
    }


def test_extraction_dual_write(client):
    """fact_type=relationship 的条目:facts 与 relationships 双写。"""
    from app.db.models import Entity, Fact, Relationship
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    headers = _auth(client, "rel_dual")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        stats = BibleService(db, p["id"]).apply_extraction(
            2, {"fact_changes": [_rel_change("张三", "李四", "兄妹")]}
        )
        db.commit()
        assert stats["facts"] == 1
        assert stats["relationships"] == 1

        fact = db.query(Fact).filter(Fact.project_id == p["id"]).one()
        assert fact.fact_type == "relationship"
        assert fact.content == "兄妹"
        assert fact.valid_from == 2

        rel = db.query(Relationship).filter(Relationship.project_id == p["id"]).one()
        assert rel.relation == "兄妹"
        assert rel.valid_from == 2
        assert rel.valid_until is None
        names = {
            db.get(Entity, rel.from_entity_id).name,
            db.get(Entity, rel.to_entity_id).name,
        }
        assert names == {"张三", "李四"}
    finally:
        db.close()


def test_relationship_temporal_update(client):
    """同实体对(不分方向)再抽新关系:旧边关区间,新边开区间;同内容重抽不重复。"""
    from app.db.models import Relationship
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    headers = _auth(client, "rel_temporal")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        bible = BibleService(db, p["id"])
        bible.apply_extraction(
            2, {"fact_changes": [_rel_change("王五", "赵六", "结拜兄弟")]}
        )
        db.commit()

        # 同内容重抽:幂等,不产生新边
        stats = bible.apply_extraction(
            3, {"fact_changes": [_rel_change("王五", "赵六", "结拜兄弟")]}
        )
        db.commit()
        assert stats["relationships"] == 0

        # 第 5 章反目:旧边 valid_until=4,新边开区间(方向反过来也算同一对)
        stats = bible.apply_extraction(
            5, {"fact_changes": [_rel_change("赵六", "王五", "反目成仇")]}
        )
        db.commit()
        assert stats["relationships"] == 1

        edges = (
            db.query(Relationship)
            .filter(Relationship.project_id == p["id"])
            .order_by(Relationship.valid_from)
            .all()
        )
        assert len(edges) == 2
        old, new = edges
        assert old.relation == "结拜兄弟"
        assert old.valid_from == 2 and old.valid_until == 4
        assert new.relation == "反目成仇"
        assert new.valid_from == 5 and new.valid_until is None
    finally:
        db.close()


def test_purge_chapter_extraction_relationships(client):
    """重写章节时:本章新开的关系边删除,被本章关闭的旧边重开。"""
    from app.db.models import Relationship
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    headers = _auth(client, "rel_purge")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        bible = BibleService(db, p["id"])
        bible.apply_extraction(
            2, {"fact_changes": [_rel_change("甲", "乙", "同门")]}
        )
        bible.apply_extraction(
            5, {"fact_changes": [_rel_change("甲", "乙", "决裂")]}
        )
        db.commit()

        stats = bible.purge_chapter_extraction(5)
        db.commit()
        assert stats["relationships_removed"] == 1
        assert stats["relationships_reopened"] == 1

        edges = db.query(Relationship).filter(Relationship.project_id == p["id"]).all()
        assert len(edges) == 1
        assert edges[0].relation == "同门"
        assert edges[0].valid_until is None
    finally:
        db.close()


def test_hard_constraints_injects_only_present_pairs(client):
    """生成注入:只注入双方都在本章出场名单内、且未退场的当前有效关系边。"""
    from app.db.models import Entity
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    headers = _auth(client, "rel_inject")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        bible = BibleService(db, p["id"])
        bible.apply_extraction(
            2,
            {
                "fact_changes": [
                    _rel_change("郭靖", "黄蓉", "恋人"),
                    _rel_change("郭靖", "杨康", "结义兄弟"),
                ]
            },
        )
        db.commit()

        block = bible.hard_constraints_block(3, ["郭靖", "黄蓉"])
        assert "· 关系: 郭靖→黄蓉: 恋人(自第2章起)" in block
        # 杨康不在出场名单 → 其与郭靖的边不注入
        assert "杨康" not in block

        # 单边名单 / 无名单 → 不注入任何关系行
        assert "关系:" not in bible.hard_constraints_block(3, ["郭靖"])
        assert "关系:" not in bible.hard_constraints_block(3)

        # 历史时刻(第 1 章,关系尚未生效)→ 不注入
        assert "关系:" not in bible.hard_constraints_block(1, ["郭靖", "黄蓉"])

        # 一方退场 → 该边不注入
        yang = (
            db.query(Entity)
            .filter(Entity.project_id == p["id"], Entity.name == "杨康")
            .one()
        )
        yang.retired = True
        db.commit()
        block = bible.hard_constraints_block(3, ["郭靖", "黄蓉", "杨康"])
        assert "· 关系: 郭靖→黄蓉: 恋人(自第2章起)" in block
        assert "杨康" not in block
    finally:
        db.close()


def test_characters_relations_api(client):
    """GET /characters:人物卡带 relations;对方退场不过滤但标记 other_retired。"""
    from app.db.models import Entity, Relationship
    from app.db.session import SessionLocal

    headers = _auth(client, "rel_card")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        a = Entity(project_id=p["id"], entity_type="character", name="令狐冲",
                   aliases=[], base_profile={})
        b = Entity(project_id=p["id"], entity_type="character", name="任盈盈",
                   aliases=[], base_profile={})
        ghost = Entity(project_id=p["id"], entity_type="character", name="风清扬",
                       aliases=[], base_profile={}, retired=True)
        db.add_all([a, b, ghost])
        db.flush()
        db.add_all([
            Relationship(project_id=p["id"], from_entity_id=a.id, to_entity_id=b.id,
                         relation="恋人", valid_from=3),
            Relationship(project_id=p["id"], from_entity_id=ghost.id, to_entity_id=a.id,
                         relation="师徒", valid_from=1),
            # 已失效的旧边不应出现在人物卡
            Relationship(project_id=p["id"], from_entity_id=a.id, to_entity_id=b.id,
                         relation="萍水相逢", valid_from=1, valid_until=2),
        ])
        db.commit()
    finally:
        db.close()

    body = client.get(f"/api/projects/{p['id']}/characters", headers=headers).json()
    cards = {c["name"]: c for c in body["characters"]}

    linghu = cards["令狐冲"]
    rels = {(r["other_name"], r["description"]): r for r in linghu["relations"]}
    assert ("任盈盈", "恋人") in rels
    assert rels[("任盈盈", "恋人")]["valid_from"] == 3
    assert rels[("任盈盈", "恋人")]["other_retired"] is False
    # 作为 to 端也能看到边;对方已退场 → 标记但不过滤
    assert ("风清扬", "师徒") in rels
    assert rels[("风清扬", "师徒")]["other_retired"] is True
    # 已失效的边不出现
    assert ("任盈盈", "萍水相逢") not in rels

    # 另一端对称可见
    ren = cards["任盈盈"]
    assert any(
        r["other_name"] == "令狐冲" and r["description"] == "恋人"
        for r in ren["relations"]
    )


def test_relations_not_owner_404(client):
    """非 owner 访问他人项目人物卡(含 relations)→ 404,不泄露关系数据。"""
    from app.db.models import Entity, Relationship
    from app.db.session import SessionLocal

    a = _auth(client, "rel_owner_a")
    b = _auth(client, "rel_owner_b")
    p = _create_project(client, a)

    db = SessionLocal()
    try:
        x = Entity(project_id=p["id"], entity_type="character", name="甲某",
                   aliases=[], base_profile={})
        y = Entity(project_id=p["id"], entity_type="character", name="乙某",
                   aliases=[], base_profile={})
        db.add_all([x, y])
        db.flush()
        db.add(Relationship(project_id=p["id"], from_entity_id=x.id,
                            to_entity_id=y.id, relation="仇敌", valid_from=1))
        db.commit()
    finally:
        db.close()

    assert client.get(
        f"/api/projects/{p['id']}/characters", headers=b
    ).status_code == 404

    # owner 自己能读到
    body = client.get(f"/api/projects/{p['id']}/characters", headers=a).json()
    assert any(
        r["description"] == "仇敌"
        for c in body["characters"] for r in c["relations"]
    )
