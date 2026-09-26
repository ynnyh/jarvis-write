# tests/test_inspire_pitches.py
# -*- coding: utf-8 -*-
"""方向提案端点(🎲 点子兜底,开书对话式确认流的发散层)。

覆盖:
- /api/inspire/pitches:解析提案(100-150 字方向+差异标签)、count 截断
- avoid_pitches 注入 prompt(「再来一组」不趋同)
- feedback 注入 prompt(带话出提案,最高优先级)
- 走 FAST 档(SUMMARY task)
- /api/inspire/pitches/async 异步跑通
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    """注册并返回响应体(含 token);头用 _h() 包。"""
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _h(u: dict) -> dict:
    return {"Authorization": f"Bearer {u['token']}"}


class _FakeAdapter:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_prompt = ""

    async def ask(self, prompt: str, **kw) -> str:  # noqa: ANN003
        self.last_prompt = prompt
        return self.payload


def _patch_pitches(monkeypatch, payload: str) -> _FakeAdapter:
    from app.api import inspire as inspire_mod

    adapter = _FakeAdapter(payload)
    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task, **kw: adapter)
    return adapter


_PITCHES_JSON = json.dumps({"pitches": [
    {"pitch": "落魄镖师接下最后一趟不许开箱的险镖,开箱发现活人,靠老底子绝活护到底",
     "label": "小人物·生计·燃"},
    {"pitch": "退休刑警发现旧案全是冤案,带老花镜重查,警局不认家属已放弃",
     "label": "老手·立场·冷"},
    {"pitch": "外卖骑手捡到只能拨给一年后自己的手机,每单都在和时间抢跑",
     "label": "骑手·奇幻·爽"},
]}, ensure_ascii=False)


def test_pitches_parses_and_fast_task(client, monkeypatch):
    u = _auth(client, "pitch_user1")
    from app.api import inspire as inspire_mod

    seen_tasks: list = []
    adapter = _FakeAdapter(_PITCHES_JSON)

    def spy(task, **kw):
        seen_tasks.append(task)
        return adapter

    monkeypatch.setattr(inspire_mod, "get_adapter_for", spy)
    r = client.post("/api/inspire/pitches", headers=_h(u),
                    json={"spark": "按「武侠」的套路来", "tendency": {"genre": "武侠"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["pitches"]) == 3
    assert data["pitches"][0]["label"] == "小人物·生计·燃"
    assert seen_tasks[-1].value == "summary"  # FAST 档


def test_pitches_avoid_block_injected(client, monkeypatch):
    """「再来一组」:上一批提案进 avoid 块,逼模型换轴。"""
    u = _auth(client, "pitch_user2")
    adapter = _patch_pitches(monkeypatch, _PITCHES_JSON)
    r = client.post("/api/inspire/pitches", headers=_h(u), json={
        "spark": "按「都市」的套路来",
        "avoid_pitches": ["落魄镖师接下最后一趟不许开箱的险镖"],
    })
    assert r.status_code == 200, r.text
    assert "上一批提案用户都没选中" in adapter.last_prompt
    assert "落魄镖师" in adapter.last_prompt


def test_pitches_feedback_block_injected(client, monkeypatch):
    """带话出提案:修改要求以最高优先级进 prompt。"""
    u = _auth(client, "pitch_user3")
    adapter = _patch_pitches(monkeypatch, _PITCHES_JSON)
    r = client.post("/api/inspire/pitches", headers=_h(u), json={
        "spark": "按「都市」的套路来",
        "feedback": "不要系统流,想要女主搞事业",
    })
    assert r.status_code == 200, r.text
    assert "修改要求" in adapter.last_prompt
    assert "不要系统流" in adapter.last_prompt


def test_pitches_async_flow(client, monkeypatch):
    """异步版:job 跑完回 pitches。"""
    u = _auth(client, "pitch_user4")
    _patch_pitches(monkeypatch, _PITCHES_JSON)
    r = client.post("/api/inspire/pitches/async", headers=_h(u),
                    json={"spark": "按「武侠」的套路来"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 30
    while True:
        job = client.get(f"/api/jobs/{job_id}", headers=_h(u)).json()
        assert job["status"] != "error", job
        if job["status"] == "done":
            break
        assert time.monotonic() < deadline, f"job 超时: {job}"
        time.sleep(0.02)
    assert len(job["result"]["pitches"]) == 3


def test_pitches_rejects_bad_count(client):
    """count 越界被 pydantic 拦下(2-5),不烧 LLM。"""
    u = _auth(client, "pitch_user5")
    r = client.post("/api/inspire/pitches", headers=_h(u),
                    json={"spark": "x", "count": 9})
    assert r.status_code == 422
