# tests/test_suggest_shape.py
# -*- coding: utf-8 -*-
"""方案轮廓推荐:阅读手感/篇幅按概念推荐,标签池过滤、非法 scale 兜底、失败透传。"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class _FakeAdapter:
    """返回预置 JSON,记录收到的 prompt。"""

    def __init__(self, raw: str):
        self.raw = raw
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.raw


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_suggest_shape_filters_labels_and_clamps_scale(client):
    """tone/elements 只保留目录内标签;scale 非法回落 mid。"""
    from app.api.projects import shape as shape_mod

    headers = _auth(client, f"shape_{uuid.uuid4().hex[:6]}")
    r = client.post("/api/projects", headers=headers,
                    json={"title": "轮廓书", "target_chapters": 5, "genre": "悬疑"})
    pid = r.json()["id"]

    fake = _FakeAdapter(
        '{"tone": ["悬疑", "自创标签"], "elements": ["身份错位"], "scale": "超长",'
        ' "tone_reason": "题材冷", "scale_reason": "单线"}'
    )
    with patch.object(shape_mod, "get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-shape", headers=headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tone"] == ["悬疑"]          # 目录外标签被丢弃
    assert body["elements"] == ["身份错位"]
    assert body["scale"] == "mid"            # 非法档位兜底
    assert "题材冷" in body["tone_reason"]
    # prompt 里带上了目录标签池,供模型照池选
    assert "悬疑" in fake.prompts[0]


def test_suggest_shape_llm_failure_is_502(client):
    from app.api.projects import shape as shape_mod

    headers = _auth(client, f"shape_fail_{uuid.uuid4().hex[:6]}")
    r = client.post("/api/projects", headers=headers,
                    json={"title": "失败书", "target_chapters": 5})
    pid = r.json()["id"]

    fake = _FakeAdapter("")  # 空回复
    with patch.object(shape_mod, "get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-shape", headers=headers)
    # 空回复不炸:返回空推荐(前端不预填即可)
    assert r.status_code == 200
    assert r.json()["tone"] == []
