# tests/test_overview.py
# -*- coding: utf-8 -*-
"""全书概览聚合接口:结构、版本对照字段、归属 404。"""
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


def _create_project(client: TestClient, headers: dict, title: str = "概览书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _seed(client: TestClient, headers: dict) -> dict:
    """造 3 章项目:大纲 1-3 章;正文 1 章定稿(基于 v2)、2 章草稿、3 章未生成;
    第 3 章大纲已到 v4 且出场名单含"花满楼";一条伏笔、一个人物。"""
    from app.db.models import Chapter, Entity, Fact, Foreshadowing, Outline
    from app.db.session import SessionLocal

    p = _create_project(client, headers)
    db = SessionLocal()
    try:
        for n in (1, 2, 3):
            db.add(
                Outline(
                    project_id=p["id"], chapter_number=n, title=f"第{n}章标题",
                    chapter_role="铺垫" if n == 1 else ("冲突" if n == 2 else "高潮"),
                    current_version=4 if n == 3 else 1,
                    characters_involved=["花满楼"] if n == 3 else [],
                )
            )
        db.add(
            Chapter(
                project_id=p["id"], chapter_number=1, status="finalized",
                final_content="正文一", word_count=3000, outline_version_used=1,
            )
        )
        db.add(
            Chapter(
                project_id=p["id"], chapter_number=2, status="drafted",
                draft_content="草稿二", word_count=1500, outline_version_used=1,
            )
        )
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
            Foreshadowing(
                project_id=p["id"], description="玉佩的来历",
                chapter_planted=1, expected_payoff_chapter=3,
                status="planted",
            )
        )
        db.commit()
    finally:
        db.close()
    return p


def test_overview_structure(client):
    """聚合结构:章节行 = 大纲 × 正文;人物出场 = 事实 ∪ 大纲名单;伏笔字段映射。"""
    headers = _auth(client, "ov_struct")
    p = _seed(client, headers)

    r = client.get(f"/api/projects/{p['id']}/overview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    chapters = body["chapters"]
    assert [c["chapter_number"] for c in chapters] == [1, 2, 3]

    c1, c2, c3 = chapters
    assert c1["title"] == "第1章标题"
    assert c1["chapter_role"] == "铺垫"
    assert c1["status"] == "finalized"
    assert c1["word_count"] == 3000
    assert c1["outline_version_used"] == 1

    assert c2["status"] == "drafted"
    assert c2["word_count"] == 1500

    # 未生成:empty,版本字段为 None
    assert c3["status"] == "empty"
    assert c3["word_count"] == 0
    assert c3["outline_version_used"] is None
    assert c3["characters_involved"] == ["花满楼"]

    # 人物出场:事实 source_chapter(2) ∪ 大纲名单(3)
    chars = body["characters"]
    assert len(chars) == 1
    assert chars[0]["name"] == "花满楼"
    assert chars[0]["retired"] is False
    assert chars[0]["chapters"] == [2, 3]

    fs = body["foreshadowings"]
    assert len(fs) == 1
    assert fs[0] == {
        "content": "玉佩的来历",
        "status": "planted",
        "planted_chapter": 1,
        "expected_chapter": 3,
        "resolved_chapter": None,
    }


def test_overview_version_compare(client):
    """版本对照:正文基于的版本与大纲当前版本同帧返回,前端可判失配。"""
    headers = _auth(client, "ov_ver")
    p = _seed(client, headers)
    # 把第 1 章正文标成基于 v1,而第 1 章大纲升到 v2 → 不一致
    from app.db.models import Outline
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        o = (
            db.query(Outline)
            .filter(Outline.project_id == p["id"], Outline.chapter_number == 1)
            .first()
        )
        o.current_version = 2
        db.commit()
    finally:
        db.close()

    body = client.get(f"/api/projects/{p['id']}/overview", headers=headers).json()
    c1 = next(c for c in body["chapters"] if c["chapter_number"] == 1)
    assert c1["outline_version_used"] == 1
    assert c1["outline_current_version"] == 2

    c3 = next(c for c in body["chapters"] if c["chapter_number"] == 3)
    assert c3["outline_current_version"] == 4


def test_overview_not_owner_404(client):
    """非 owner 访问他人项目概览 → 404(不泄露存在性);未登录 → 401。"""
    a = _auth(client, "ov_owner_a")
    b = _auth(client, "ov_owner_b")
    p = _create_project(client, a)

    assert client.get(
        f"/api/projects/{p['id']}/overview", headers=b
    ).status_code == 404
    assert client.get(f"/api/projects/{p['id']}/overview").status_code == 401
    assert client.get(
        "/api/projects/999999/overview", headers=a
    ).status_code == 404
