# tests/test_upload_limits.py
# -*- coding: utf-8 -*-
"""上传体积闸门:全局 Content-Length 总闸 + JSON 导入自己的上限。

两道闸门分工:
- main.MaxBodySizeMiddleware(128MB):总闸,读 body 之前就 413,不进内存;
- api.project_io.MAX_IMPORT_JSON_BYTES(64MB):JSON 导入自己的口径,超了给更
  具体的中文提示(端点侧限量读,而不是"全读完再判大小")。

只管请求方向:响应方向的大流量(SSE 流式、整本导出下载)不受影响。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import project_io
from app.main import MAX_REQUEST_BODY_BYTES, MaxBodySizeMiddleware, app

INVITE = "test-invite"


# ---------- 全局总闸(用小阈值独立 app 验证,不必真造 128MB 请求) ----------


def _capped_client(max_bytes: int = 1024) -> TestClient:
    api = FastAPI()
    api.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)

    @api.post("/echo")
    async def echo(request: Request):
        return {"len": len(await request.body())}

    @api.get("/big-response")
    def big_response():
        # 响应方向的大流量不受请求侧闸门影响(SSE/整本导出同理)
        return {"blob": "x" * 4096}

    return TestClient(api)


def test_oversized_content_length_rejected_413():
    c = _capped_client()
    r = c.post("/echo", content=b"a" * 2048)
    assert r.status_code == 413
    assert "超过" in r.json()["detail"]


def test_normal_request_passes():
    c = _capped_client()
    r = c.post("/echo", content=b"a" * 512)
    assert r.status_code == 200
    assert r.json()["len"] == 512


def test_big_response_not_blocked():
    """响应体大于请求闸门也照发——闸门只管请求方向。"""
    c = _capped_client()
    r = c.get("/big-response")
    assert r.status_code == 200
    assert len(r.json()["blob"]) == 4096


def test_app_wires_body_limit_middleware():
    """真 app 上确实挂着这道闸,且阈值就是 128MB。"""
    assert MAX_REQUEST_BODY_BYTES == 128 * 1024 * 1024
    assert any(m.cls is MaxBodySizeMiddleware for m in app.user_middleware)


def test_json_import_limit_constant():
    assert project_io.MAX_IMPORT_JSON_BYTES == 64 * 1024 * 1024


# ---------- JSON 导入端点侧(把上限临时压小,钉住同一条代码路径) ----------


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient) -> dict:
    r = client.post(
        "/api/auth/register",
        json={
            "username": f"upl_{uuid.uuid4().hex[:8]}",
            "password": "pass123",
            "invite_code": INVITE,
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_json_import_over_limit_returns_413(client, monkeypatch):
    headers = _auth(client)
    monkeypatch.setattr(project_io, "MAX_IMPORT_JSON_BYTES", 1024)
    r = client.post(
        "/api/projects/import",
        headers=headers,
        files={"file": ("big.json", b'{"x": "' + b"y" * 4096 + b'"}', "application/json")},
    )
    assert r.status_code == 413
    assert "上限" in r.json()["detail"]


def test_json_import_normal_file_still_imports(client, monkeypatch):
    """闸门不能误伤正常导入:限额下方的 JSON 依旧走通(往返仍是原来那样)。"""
    headers = _auth(client)
    pid = client.post("/api/projects", headers=headers, json={"title": "上传闸门书"}).json()["id"]
    exported = client.get(f"/api/projects/{pid}/export", headers=headers)
    assert exported.status_code == 200

    monkeypatch.setattr(project_io, "MAX_IMPORT_JSON_BYTES", len(exported.content) + 1024)
    r = client.post(
        "/api/projects/import",
        headers=headers,
        files={"file": ("p.json", exported.content, "application/json")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "项目导入成功"


def test_json_import_invalid_json_still_400(client):
    """超限判定排在解析之前,但非法 JSON 仍是原来的 400 文案。"""
    headers = _auth(client)
    r = client.post(
        "/api/projects/import",
        headers=headers,
        files={"file": ("bad.json", b"not json at all", "application/json")},
    )
    assert r.status_code == 400
    assert "不是有效的 JSON" in r.json()["detail"]
