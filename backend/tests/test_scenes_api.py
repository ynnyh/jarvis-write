# tests/test_scenes_api.py
# -*- coding: utf-8 -*-
"""场景接口契约(§2.6 可干预性)。

钉住四件事:
1. 能列出本章场景卡(不含正文,看板只要卡片);
2. 改场景卡只动 CARD_FIELDS,改完已生成的场次回到 planned(待作者决定是否重生成);
3. 手改场景正文存版本快照 + 递增版本,内容没变则不重复存;
4. 越权/不存在一律 404,不泄漏别的项目的场景。

数据用 app 自己的 session 种(与其它 API 测试同一口径:共享 app 单例 + conftest 的临时库),
不 override get_db——那样会绕过 app 启动时的建表。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _session():
    from app.db.session import SessionLocal

    return SessionLocal()


def _auth(client: TestClient) -> dict:
    name = f"sc_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/auth/register",
        json={"username": name, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _project_with_scenes(client: TestClient, headers: dict, *, n=3):
    """建项目 + 大纲 + n 张场景卡(第 1 场已有正文,模拟「已生成一半」)。

    返回 (pid, scene_ids)。
    """
    from app.db.models import Outline, Project, Scene

    r = client.post("/api/projects", headers=headers,
                    json={"title": "场景接口", "target_chapters": 10})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    db = _session()
    try:
        assert db.get(Project, pid) is not None
        outline = Outline(project_id=pid, chapter_number=1, title="第一章",
                          summary="夜探敌营", chapter_role="铺垫")
        db.add(outline)
        db.commit()

        ids = []
        for i in range(1, n + 1):
            s = Scene(
                project_id=pid, outline_id=outline.id, chapter_number=1, seq=i,
                title=f"第{i}场", location=f"地点{i}", characters=["林昭"],
                goal=f"目标{i}", conflict=f"冲突{i}", emotion_target="紧绷",
                tension_level=3, target_words=1500,
                content=("已写好的正文。" if i == 1 else ""),
                word_count=(7 if i == 1 else 0),
                status=("accepted" if i == 1 else "planned"),
            )
            db.add(s)
            db.flush()
            ids.append(s.id)
        db.commit()
    finally:
        db.close()
    return pid, ids


def test_list_scenes_returns_cards_without_text(client):
    headers = _auth(client)
    pid, _ = _project_with_scenes(client, headers)

    r = client.get(f"/api/projects/{pid}/scenes/1", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_count"] == 3
    assert [s["seq"] for s in body["scenes"]] == [1, 2, 3]
    # 列表不带正文:看板只要卡片
    assert all("content" not in s for s in body["scenes"])
    assert body["scenes"][0]["status"] == "accepted"


def test_list_scenes_empty_when_not_planned(client):
    headers = _auth(client)
    r = client.post("/api/projects", headers=headers,
                    json={"title": "未分场", "target_chapters": 10})
    pid = r.json()["id"]

    r = client.get(f"/api/projects/{pid}/scenes/1", headers=headers)
    assert r.status_code == 200
    assert r.json()["scene_count"] == 0


def test_get_scene_detail_includes_text(client):
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.get(f"/api/projects/{pid}/scenes/detail/{ids[0]}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "已写好的正文。"


def test_patch_scene_card_updates_fields(client):
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.patch(
        f"/api/projects/{pid}/scenes/{ids[1]}",
        headers=headers,
        json={"goal": "改成撞破真相", "emotion_target": "窒息", "tension_level": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["changed"]) == {"goal", "emotion_target", "tension_level"}
    assert body["scene"]["goal"] == "改成撞破真相"
    assert body["scene"]["tension_level"] == 5


def test_patch_marks_written_scene_planned(client):
    """改卡后已有正文视为过时 → 回 planned,等作者决定是否重生成。

    不自动重写:改卡是意图表达,重生成是花钱的动作,该由作者点。
    """
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.patch(
        f"/api/projects/{pid}/scenes/{ids[0]}", headers=headers, json={"goal": "新目标"}
    )
    assert r.status_code == 200
    assert r.json()["scene"]["status"] == "planned"
    # 正文本身不动(过时标记,不是清空)
    assert r.json()["scene"]["content"] == "已写好的正文。"


def test_patch_ignores_non_card_fields(client):
    """content/status 不属于场景卡,不能被 PATCH 偷偷改掉。"""
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.patch(
        f"/api/projects/{pid}/scenes/{ids[0]}",
        headers=headers,
        json={"content": "伪造正文", "status": "discarded", "goal": "改目标"},
    )
    assert r.status_code == 200, r.text
    assert "content" not in r.json()["changed"]
    assert r.json()["scene"]["content"] == "已写好的正文。"


def test_patch_no_change_reports_empty(client):
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.patch(
        f"/api/projects/{pid}/scenes/{ids[1]}",
        headers=headers, json={"goal": "目标2"},  # 与原值相同
    )
    assert r.status_code == 200
    assert r.json()["changed"] == []


def test_patch_scene_card_validates_tension_range(client):
    """张力档越界要被挡住(1-5),不能写进库里让下游 prompt 拿到非法值。"""
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.patch(
        f"/api/projects/{pid}/scenes/{ids[1]}",
        headers=headers, json={"tension_level": 9},
    )
    assert r.status_code == 422


def test_update_scene_text_snapshots(client):
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    before = client.get(f"/api/projects/{pid}/scenes/detail/{ids[0]}", headers=headers)
    old_version = before.json()["version"]

    r = client.put(
        f"/api/projects/{pid}/scenes/{ids[0]}/text",
        headers=headers, json={"content": "作者亲手改过的正文。", "note": "改语感"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is True
    assert r.json()["scene"]["content"] == "作者亲手改过的正文。"
    assert r.json()["scene"]["version"] == old_version + 1

    from app.db.models import SceneVersion

    db = _session()
    try:
        snaps = (
            db.query(SceneVersion).filter(SceneVersion.scene_id == ids[0]).all()
        )
        assert len(snaps) == 1
        assert snaps[0].content == "已写好的正文。"
        assert snaps[0].source == "edited"
    finally:
        db.close()


def test_update_scene_text_idempotent(client):
    """内容没变不存快照(否则每点一次保存就多一版无意义的记录)。"""
    headers = _auth(client)
    pid, ids = _project_with_scenes(client, headers)

    r = client.put(
        f"/api/projects/{pid}/scenes/{ids[0]}/text",
        headers=headers, json={"content": "已写好的正文。"},
    )
    assert r.status_code == 200
    assert r.json()["changed"] is False

    from app.db.models import SceneVersion

    db = _session()
    try:
        assert (
            db.query(SceneVersion).filter(SceneVersion.scene_id == ids[0]).count() == 0
        )
    finally:
        db.close()


def test_scene_404_for_missing(client):
    headers = _auth(client)
    pid, _ = _project_with_scenes(client, headers)

    assert client.get(
        f"/api/projects/{pid}/scenes/detail/999999", headers=headers
    ).status_code == 404
    assert client.patch(
        f"/api/projects/{pid}/scenes/999999", headers=headers, json={"goal": "x"}
    ).status_code == 404


def test_scene_404_across_projects(client):
    """别的项目的场景不能被本项目的路径访问到(防越权)。"""
    headers = _auth(client)
    _, ids_a = _project_with_scenes(client, headers)
    pid_b, _ = _project_with_scenes(client, headers)

    r = client.get(
        f"/api/projects/{pid_b}/scenes/detail/{ids_a[0]}", headers=headers
    )
    assert r.status_code == 404


def test_scene_404_for_missing_project(client):
    headers = _auth(client)
    assert client.get("/api/projects/999999/scenes/1", headers=headers).status_code == 404
