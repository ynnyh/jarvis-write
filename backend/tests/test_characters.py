# tests/test_characters.py
# -*- coding: utf-8 -*-
"""人物管理闭环:新增/退场/恢复/删事实,以及退场实体不再进入生成注入。"""
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


def _create_project(client: TestClient, headers: dict, title: str = "人物书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_character_roundtrip(client):
    """新增人物:实体 + 初始 fact 落库,GET 人物卡读回。"""
    headers = _auth(client, "char_create")
    p = _create_project(client, headers)

    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "沈孤鸿", "aliases": ["沈楼主"], "profile": "听雪楼主,剑术通神"},
    )
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["name"] == "沈孤鸿"
    assert card["aliases"] == ["沈楼主"]
    assert card["retired"] is False
    assert card["profile"] == "听雪楼主,剑术通神"
    assert len(card["key_facts"]) == 1
    fact = card["key_facts"][0]
    assert fact["fact_type"] == "state"
    assert fact["content"] == "听雪楼主,剑术通神"
    assert fact["valid_from"] == 1
    assert fact["importance"] == "normal"

    r = client.get(f"/api/projects/{p['id']}/characters", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["characters"]) == 1
    assert body["characters"][0]["name"] == "沈孤鸿"
    assert body["other_entities_count"] == 0


def test_create_character_default_fact(client):
    """不带简介时初始 fact 内容为"初始登记"。"""
    headers = _auth(client, "char_default")
    p = _create_project(client, headers)

    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "路人甲"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["key_facts"][0]["content"] == "初始登记"


def test_create_character_duplicate_400(client):
    """重名(含别名命中)→ 400。"""
    headers = _auth(client, "char_dup")
    p = _create_project(client, headers)
    client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "叶孤城", "aliases": ["白云城主"]},
    )

    # 同名
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "叶孤城"},
    )
    assert r.status_code == 400
    # 名字命中已有别名
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "白云城主"},
    )
    assert r.status_code == 400
    # 新别名命中已有名字
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "西门吹雪", "aliases": ["叶孤城"]},
    )
    assert r.status_code == 400
    # 空名字
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "   "},
    )
    assert r.status_code == 400


def test_retired_excluded_from_hard_constraints(client):
    """退场后:hard_constraints_block 不再注入该人物;恢复后回来。"""
    from app.db.models import Entity, Fact
    from app.db.session import SessionLocal
    from app.engines.consistency import BibleService

    headers = _auth(client, "char_retire")
    p = _create_project(client, headers)
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "楚留香", "profile": "盗帅,轻功天下第一"},
    )
    ent_id = r.json()["id"]

    def _block() -> str:
        db = SessionLocal()
        try:
            return BibleService(db, p["id"]).hard_constraints_block(5)
        finally:
            db.close()

    assert "楚留香" in _block()

    # 退场
    r = client.patch(
        f"/api/projects/{p['id']}/characters/{ent_id}",
        headers=headers,
        json={"retired": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["retired"] is True
    assert "楚留香" not in _block()

    # 数据保留:人物卡仍能查到事实
    card = client.get(
        f"/api/projects/{p['id']}/characters", headers=headers
    ).json()["characters"][0]
    assert card["retired"] is True
    assert len(card["key_facts"]) == 1

    # 恢复
    r = client.patch(
        f"/api/projects/{p['id']}/characters/{ent_id}",
        headers=headers,
        json={"retired": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["retired"] is False
    assert "楚留香" in _block()

    # 直接落库一个退场实体,验证引擎层过滤(不经过接口)
    db = SessionLocal()
    try:
        ghost = Entity(
            project_id=p["id"], entity_type="character",
            name="幽灵人", aliases=[], base_profile={}, retired=True,
        )
        db.add(ghost)
        db.flush()
        db.add(
            Fact(
                project_id=p["id"], entity_id=ghost.id, fact_type="state",
                content="不该出现的状态", valid_from=1, importance="critical",
                source_chapter=1,
            )
        )
        db.commit()
    finally:
        db.close()
    assert "幽灵人" not in _block()
    assert "不该出现的状态" not in _block()


def test_appearance_chapters_from_outline(client):
    """出场章号 = facts.source_chapter ∪ outlines.characters_involved 命中。"""
    from app.db.models import Entity, Fact, Outline
    from app.db.session import SessionLocal

    headers = _auth(client, "char_appr")
    p = _create_project(client, headers)
    db = SessionLocal()
    try:
        ent = Entity(
            project_id=p["id"], entity_type="character",
            name="花满楼", aliases=[], base_profile={},
        )
        db.add(ent)
        db.flush()
        db.add(
            Fact(
                project_id=p["id"], entity_id=ent.id, fact_type="state",
                content="双目失明", valid_from=2, importance="major",
                source_chapter=2,
            )
        )
        db.add(
            Outline(
                project_id=p["id"], chapter_number=3, title="第三章",
                characters_involved=["花满楼", "陆小凤"],
            )
        )
        db.commit()
    finally:
        db.close()

    body = client.get(
        f"/api/projects/{p['id']}/characters", headers=headers
    ).json()
    card = next(c for c in body["characters"] if c["name"] == "花满楼")
    assert card["appearance_chapters"] == [2, 3]


def test_delete_fact(client):
    """删除抽错的事实:DELETE 后人物卡不再包含它;重复删 → 404。"""
    headers = _auth(client, "char_delfact")
    p = _create_project(client, headers)
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=headers,
        json={"name": "李寻欢", "profile": "小李飞刀,例不虚发"},
    )
    assert r.status_code == 200, r.text
    fact_id = r.json()["key_facts"][0]["id"]

    r = client.delete(f"/api/projects/{p['id']}/facts/{fact_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    card = client.get(
        f"/api/projects/{p['id']}/characters", headers=headers
    ).json()["characters"][0]
    assert card["key_facts"] == []

    assert client.delete(
        f"/api/projects/{p['id']}/facts/{fact_id}", headers=headers
    ).status_code == 404


def test_characters_not_owner_404(client):
    """非 owner 访问/操作他人项目的人物与事实 → 404(不泄露存在性)。"""
    a = _auth(client, "char_owner_a")
    b = _auth(client, "char_owner_b")
    p = _create_project(client, a)
    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=a,
        json={"name": "独孤求败"},
    )
    ent_id = r.json()["id"]
    fact_id = r.json()["key_facts"][0]["id"]

    assert client.get(
        f"/api/projects/{p['id']}/characters", headers=b
    ).status_code == 404
    assert client.post(
        f"/api/projects/{p['id']}/characters", headers=b, json={"name": "x"}
    ).status_code == 404
    assert client.patch(
        f"/api/projects/{p['id']}/characters/{ent_id}",
        headers=b,
        json={"retired": True},
    ).status_code == 404
    assert client.delete(
        f"/api/projects/{p['id']}/facts/{fact_id}", headers=b
    ).status_code == 404

    # 本人项目里不存在的人物 id → 404
    assert client.patch(
        f"/api/projects/{p['id']}/characters/999999",
        headers=a,
        json={"retired": True},
    ).status_code == 404
