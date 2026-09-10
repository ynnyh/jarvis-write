# tests/test_tension_curve_api.py
# -*- coding: utf-8 -*-
"""全书张力曲线的 API 契约:前端画节奏曲线靠它。

钉住:
1. 曲线长度 = 目标章数,值域 1-5;
2. 报告里的 flat 字段真的能反映"平不平";
3. 没有卷纲也不崩(短篇/刚建项目)。
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


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_project(client: TestClient, headers: dict, chapters: int = 12) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": "节奏书", "target_chapters": chapters})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_curve_matches_target_chapters(client):
    headers = _auth(client, f"tc_len_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers, chapters=12)

    r = client.get(f"/api/projects/{pid}/outlines/tension-curve", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chapters"] == list(range(1, 13))
    assert len(body["tension"]) == 12
    assert all(1 <= v <= 5 for v in body["tension"]), body["tension"]


def test_curve_report_is_present(client):
    headers = _auth(client, f"tc_rep_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers, chapters=30)

    r = client.get(f"/api/projects/{pid}/outlines/tension-curve", headers=headers)
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    for key in ("span", "mean", "flat", "longest_run", "peak_at", "chapters"):
        assert key in report, key
    # 总线本身就该是有起伏的 —— 这是这个功能的全部意义
    assert report["flat"] is False
    # 峰值不该落在最后一章(最后一章是余韵)
    assert report["peak_at"] < report["chapters"]


def test_curve_works_without_macro_plan(client):
    """新项目还没有卷纲,接口也必须给出有起伏的曲线,不能 500。"""
    headers = _auth(client, f"tc_nomp_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers, chapters=8)
    r = client.get(f"/api/projects/{pid}/outlines/tension-curve", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["tension"]) == 8


def test_curve_404_for_missing_project(client):
    headers = _auth(client, f"tc_404_{uuid.uuid4().hex[:6]}")
    r = client.get("/api/projects/99999999/outlines/tension-curve", headers=headers)
    assert r.status_code == 404
