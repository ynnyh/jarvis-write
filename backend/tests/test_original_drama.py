# -*- coding: utf-8 -*-
"""原创漫剧工作区边界:独立入口可用,且不会把记录混进既有动画/小说。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": "test-invite"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create(client: TestClient, headers: dict, workspace: str, premise: str = "三个室友努力维持体面") -> int:
    r = client.post(
        "/api/anime",
        headers=headers,
        json={
            "title": f"{workspace} 作品",
            "premise": premise,
            "genre": "comedy",
            "direction": "chibi",
            "episode_s": 60,
            "workspace": workspace,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["series"]["id"]


def test_original_workspace_is_separate_from_existing_anime(client):
    headers = _auth(client, f"original_boundary_{int(time.time() * 1000)}")
    anime_id = _create(client, headers, "anime", "饭团精灵的厨房日常")
    original_id = _create(client, headers, "original")

    assert [s["id"] for s in client.get("/api/anime", headers=headers).json()["series"]] == [anime_id]
    original_rows = client.get("/api/anime?workspace=original", headers=headers)
    assert [s["id"] for s in original_rows.json()["series"]] == [original_id]
    assert client.get(f"/api/anime/{original_id}", headers=headers).status_code == 404
    assert client.get(f"/api/anime/{original_id}?workspace=original", headers=headers).status_code == 200
    assert client.get(f"/api/creative/anime/{original_id}", headers=headers).status_code == 404
    assert client.get(f"/api/creative/original/{original_id}", headers=headers).status_code == 200

    # 工作区参数本身也有白名单,避免拼写错误退化成默认 anime。
    assert client.get("/api/anime?workspace=novel", headers=headers).status_code == 400


def test_original_creation_requires_own_premise_and_novel_entry_rejects_drama(client):
    headers = _auth(client, f"original_validation_{int(time.time() * 1000)}")
    r = client.post(
        "/api/anime",
        headers=headers,
        json={"workspace": "original", "genre": "comedy", "direction": "chibi", "episode_s": 60},
    )
    assert r.status_code == 400
    r = client.post("/api/projects", headers=headers, json={"title": "误入口", "mode": "drama"})
    assert r.status_code == 400
    assert "原创漫剧" in r.json()["detail"]


def test_cross_workspace_mutation_is_rejected_without_deleting_original(client):
    headers = _auth(client, f"original_mutation_{int(time.time() * 1000)}")
    original_id = _create(client, headers, "original")
    assert client.patch(
        f"/api/anime/{original_id}",
        headers=headers,
        json={"title": "越界修改"},
    ).status_code == 404
    assert client.delete(f"/api/anime/{original_id}", headers=headers).status_code == 404
    got = client.get(f"/api/anime/{original_id}?workspace=original", headers=headers)
    assert got.status_code == 200 and got.json()["series"]["title"] == "original 作品"
