# tests/test_facts_timeline.py
# -*- coding: utf-8 -*-
"""时序事实时间线:轨道按有效事实数排序、区间原样透出、非 owner 404。"""
from __future__ import annotations

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


def _create_project(client: TestClient, headers: dict) -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": "时间线书"})
    assert r.status_code == 200, r.text
    return r.json()


def test_facts_timeline_tracks_and_spans(client):
    """轨道按当前有效事实数降序;区间字段原样透出;非角色实体不成轨。"""
    from app.db.models import Entity, Fact, Outline
    from app.db.session import SessionLocal

    headers = _auth(client, "ftl_user")
    p = _create_project(client, headers)

    db = SessionLocal()
    try:
        pid = p["id"]
        db.add(Outline(project_id=pid, chapter_number=10, title="终章", summary=""))
        a = Entity(project_id=pid, entity_type="character", name="陆辰",
                   aliases=[], base_profile={})
        b = Entity(project_id=pid, entity_type="character", name="赵岐山",
                   aliases=[], base_profile={})
        item = Entity(project_id=pid, entity_type="item", name="玉佩",
                      aliases=[], base_profile={})
        db.add_all([a, b, item])
        db.flush()
        # 陆辰 3 条有效(排第一) + 1 条已关区间;赵岐山 1 条;玉佩(非角色)不成轨
        db.add_all([
            Fact(project_id=pid, entity_id=a.id, fact_type="state",
                 content="练气六层,修为五年未进", valid_from=1, valid_until=None,
                 importance="major", source_chapter=1),
            Fact(project_id=pid, entity_id=a.id, fact_type="possession",
                 content="持有玉佩", valid_from=1, valid_until=None,
                 importance="critical", source_chapter=1),
            Fact(project_id=pid, entity_id=a.id, fact_type="relationship",
                 content="被赵岐山针对", valid_from=1, valid_until=None,
                 importance="major", source_chapter=1),
            Fact(project_id=pid, entity_id=a.id, fact_type="state",
                 content="左耳有旧伤", valid_from=1, valid_until=3,
                 importance="minor", source_chapter=1),
            Fact(project_id=pid, entity_id=b.id, fact_type="state",
                 content="外门首席,趾高气扬", valid_from=1, valid_until=None,
                 importance="minor", source_chapter=1),
        ])
        db.commit()
    finally:
        db.close()

    body = client.get(f"/api/projects/{p['id']}/facts-timeline", headers=headers).json()
    assert body["max_chapter"] == 10
    assert body["tracks"][0]["name"] == "陆辰"
    assert len(body["tracks"][0]["facts"]) == 4
    assert len(body["tracks"]) == 2  # 玉佩是 item,不成轨

    first = body["tracks"][0]["facts"][0]
    assert first["content"] == "练气六层,修为五年未进"
    assert first["valid_from"] == 1 and first["valid_until"] is None
    closed = [f for f in body["tracks"][0]["facts"] if f["content"] == "左耳有旧伤"]
    assert closed and closed[0]["valid_until"] == 3


def test_facts_timeline_empty_project(client):
    """空项目:tracks 为空、max_chapter 为 0,不报错。"""
    headers = _auth(client, "ftl_empty")
    p = _create_project(client, headers)
    body = client.get(f"/api/projects/{p['id']}/facts-timeline", headers=headers).json()
    assert body["tracks"] == []
    assert body["max_chapter"] == 0


def test_facts_timeline_not_owner_404(client):
    a = _auth(client, "ftl_owner")
    p = _create_project(client, a)
    b = _auth(client, "ftl_other")
    r = client.get(f"/api/projects/{p['id']}/facts-timeline", headers=b)
    assert r.status_code == 404
