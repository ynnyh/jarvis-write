# tests/test_soft_delete.py
# -*- coding: utf-8 -*-
"""软删(docs/29 留痕 / docs/32 §2.4):删掉的书前台不可见、后台可见、超期才物理清。

为什么值得单测:用户「删掉重来」的那一刻恰是复盘价值最高的时刻(传国玉玺案例),
软删把那一刻的证据留下来;而「前台一律不可见」又必须是硬的,否则删了还在列表里
等于没删。两条语义互相拉扯,最容易在后续改动里被悄悄破坏。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str, password: str = "pass123") -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_project(client: TestClient, headers: dict, title: str = "软删书") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_content(pid: int) -> None:
    from app.db.models import Chapter, ChapterSummary, Outline
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        outline = Outline(project_id=pid, chapter_number=1, title="第一章")
        db.add(outline)
        db.flush()
        db.add(Chapter(project_id=pid, outline_id=outline.id, chapter_number=1,
                       final_content="正文", status="approved", word_count=120))
        db.add(ChapterSummary(project_id=pid, chapter_number=1, rolling_summary="摘要"))
        db.commit()


def _make_admin(username: str) -> None:
    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        user.is_admin = True
        db.commit()


def test_delete_is_soft_and_hides_everywhere(client):
    """删除 = 置 deleted_at:列表与详情都当它不存在,数据完好留在库里。"""
    from app.db.models import Chapter, Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "soft_del_owner")["token"])
    pid = _create_project(client, headers, "要软删的书")
    _seed_content(pid)

    r = client.delete(f"/api/projects/{pid}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["soft_deleted"] is True

    # 前台:详情 404,列表不含
    assert client.get(f"/api/projects/{pid}", headers=headers).status_code == 404
    listed = client.get("/api/projects", headers=headers).json()
    assert all(p["id"] != pid for p in listed)

    # 库里:行还在(留痕),关联数据也还在
    with SessionLocal() as db:
        project = db.get(Project, pid)
        assert project is not None
        assert project.deleted_at is not None
        assert db.query(Chapter).filter_by(project_id=pid).count() == 1


def test_soft_deleted_project_is_invisible_to_other_users(client):
    """归属隔离不受软删影响:别人的书(哪怕已删)依然 404。"""
    a = _auth(_register(client, "soft_del_a")["token"])
    b = _auth(_register(client, "soft_del_b")["token"])
    pid = _create_project(client, a, "甲的软删书")
    client.delete(f"/api/projects/{pid}", headers=a)
    assert client.get(f"/api/projects/{pid}", headers=b).status_code == 404
    assert client.delete(f"/api/projects/{pid}", headers=b).status_code == 404


def test_admin_sees_deleted_project_and_its_journey(client):
    """admin 复盘通道:作品清单(含软删)+ 单本旅程时间线;非 admin 403。"""
    headers = _auth(_register(client, "soft_admin")["token"])
    pid = _create_project(client, headers, "复盘书")
    _seed_content(pid)
    assert client.delete(f"/api/projects/{pid}", headers=headers).status_code == 200
    _make_admin("soft_admin")

    assert client.get("/api/admin/projects", headers=headers).status_code == 200
    other = _auth(_register(client, "soft_nonadmin")["token"])
    assert client.get("/api/admin/projects", headers=other).status_code == 403

    rows = client.get("/api/admin/projects", headers=headers).json()
    row = next(r for r in rows if r["id"] == pid)
    assert row["deleted"] is True
    assert row["owner"] == "soft_admin"
    assert row["chapters"] == 1 and row["words"] == 120

    # 不带 include_deleted 时不含已删的书
    live_rows = client.get("/api/admin/projects?include_deleted=false", headers=headers).json()
    assert all(r["id"] != pid for r in live_rows)

    journey = client.get(f"/api/admin/projects/{pid}/journey", headers=headers)
    assert journey.status_code == 200, journey.text
    data = journey.json()
    assert data["project"]["deleted"] is True
    assert data["counts"]["chapters"] == 1
    kinds = [e["kind"] for e in data["timeline"]]
    assert "project" in kinds and "chapter" in kinds
    # 时间线必须有序(复盘要能顺着读)
    stamps = [str(e["at"]) for e in data["timeline"] if e["at"]]
    assert stamps == sorted(stamps)


def test_purge_physically_removes_only_expired_trash(client):
    """超期(>保留期)的软删书才物理清;保留期内的原样留着。"""
    from app.api.deps import PROJECT_TRASH_RETENTION_DAYS, purge_deleted_projects
    from app.db.models import Chapter, Outline, Project
    from app.db.session import SessionLocal

    assert PROJECT_TRASH_RETENTION_DAYS > 0
    headers = _auth(_register(client, "soft_purge")["token"])
    old_pid = _create_project(client, headers, "过期的书")
    fresh_pid = _create_project(client, headers, "刚删的书")
    for pid in (old_pid, fresh_pid):
        _seed_content(pid)
        assert client.delete(f"/api/projects/{pid}", headers=headers).status_code == 200

    with SessionLocal() as db:
        project = db.get(Project, old_pid)
        project.deleted_at = datetime.now(timezone.utc) - timedelta(
            days=PROJECT_TRASH_RETENTION_DAYS + 5
        )
        db.commit()

    with SessionLocal() as db:
        assert purge_deleted_projects(db) == 1

    with SessionLocal() as db:
        assert db.get(Project, old_pid) is None
        assert db.query(Chapter).filter_by(project_id=old_pid).count() == 0
        assert db.query(Outline).filter_by(project_id=old_pid).count() == 0
        assert db.get(Project, fresh_pid) is not None  # 保留期内的不动
