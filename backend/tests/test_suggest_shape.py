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
    """tone/elements 只保留目录内标签;scale 非法置空(不伪造档位)。"""
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
    assert body["scale"] == ""               # 非法档位=没有推荐,不伪造(旧版兜底 mid 会静默改掉用户默认 30 章)
    assert "题材冷" in body["tone_reason"]
    # prompt 里带上了目录标签池,供模型照池选
    assert "悬疑" in fake.prompts[0]


def test_suggest_shape_unparseable_is_502(client):
    """输出解析失败 → 502 跳过,绝不兜底成「中篇 60 章」替用户做体量决定。"""
    from app.api.projects import shape as shape_mod

    headers = _auth(client, f"shape_garbage_{uuid.uuid4().hex[:6]}")
    r = client.post("/api/projects", headers=headers,
                    json={"title": "垃圾输出书", "target_chapters": 30})
    pid = r.json()["id"]

    fake = _FakeAdapter("这不是 JSON,是模型跑题的散文。")
    with patch.object(shape_mod, "get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-shape", headers=headers)
    assert r.status_code == 502


def test_suggest_shape_llm_failure_is_502(client):
    from app.api.projects import shape as shape_mod

    headers = _auth(client, f"shape_fail_{uuid.uuid4().hex[:6]}")
    r = client.post("/api/projects", headers=headers,
                    json={"title": "失败书", "target_chapters": 5})
    pid = r.json()["id"]

    fake = _FakeAdapter("")  # 空回复
    with patch.object(shape_mod, "get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-shape", headers=headers)
    # 空回复=解析不出任何东西:502 跳过(前端静默忽略,只失去预填,不拦开书)
    assert r.status_code == 502


def test_suggest_shape_recovers_compound_labels(client):
    """模型爱在池标签上添字(「悬疑烧脑」「诙谐甜宠」):包含匹配回收,不再全军覆没。"""
    from app.api.projects import shape as shape_mod

    headers = _auth(client, f"shape_fuzzy_{uuid.uuid4().hex[:6]}")
    r = client.post("/api/projects", headers=headers,
                    json={"title": "复合标签书", "target_chapters": 5, "genre": "都市"})
    pid = r.json()["id"]

    fake = _FakeAdapter(
        '{"tone": ["悬疑烧脑", "诙谐甜宠", "浪漫张力"], "elements": ["穿越错位"], "scale": "mid",'
        ' "tone_reason": "复合味", "scale_reason": "单线"}'
    )
    with patch.object(shape_mod, "get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-shape", headers=headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tone"] == ["悬疑", "诙谐", "浪漫"]
    assert body["elements"] == ["穿越"]      # 「穿越错位」→ 穿越;身份错位不在串里,不硬凑
