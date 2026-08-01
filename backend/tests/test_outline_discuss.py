# tests/test_outline_discuss.py
# -*- coding: utf-8 -*-
"""单章大纲 AI 研讨测试:聊清"这章大纲哪里不对" → 蒸馏改写提案。

验证点:
- POST .../outlines/{n}/discuss 返回 reply + proposal(蒸馏 JSON)
- 蒸馏出"-"(无明确方向)→ proposal 为 None
- 蒸馏抛错不阻塞对话(reply 正常,proposal 置空)
- 最后一条非 user / 空对话 → 400;无大纲章 → 404;对他人项目 → 404
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.models import Chapter, Outline
from app.db.session import SessionLocal
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


def _create_project(client: TestClient, headers: dict, title: str = "大纲研讨书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_outline(project_id: int, n: int = 1, written: bool = False) -> None:
    """直接往库里塞一章蓝图(可选再塞一段定稿正文)。"""
    db = SessionLocal()
    try:
        db.add(Outline(
            project_id=project_id, chapter_number=n, title="雨夜",
            chapter_purpose="主角登场", summary="主角在雨夜登场",
            foreshadowing="埋下身世伏笔", current_version=1,
        ))
        if written:
            content = "这是第一章的正文。" * 20
            db.add(Chapter(
                project_id=project_id, chapter_number=n,
                final_content=content, status="approved", word_count=len(content),
            ))
        db.commit()
    finally:
        db.close()


class _ChatAdapter:
    """假适配器:complete 返回续聊回复,ask 返回蒸馏结果(研讨两段式)。"""

    def __init__(self, reply: str, distilled):
        self._reply = reply
        self._distilled = distilled
        self.max_tokens = 8192

    def _record_usage(self, resp):  # noqa: ANN001
        pass

    async def complete(self, messages):
        return type("R", (), {
            "content": self._reply, "model": "fake",
            "prompt_tokens": 1, "completion_tokens": 1,
        })()

    async def ask(self, prompt, system=None):
        if isinstance(self._distilled, Exception):
            raise self._distilled
        return self._distilled


def test_outline_discuss_returns_reply_and_proposal(client):
    headers = _auth(client, "ol_disc_user")
    p = _create_project(client, headers)
    _seed_outline(p["id"])

    from app.engines import outline_discuss as od_mod

    distilled = json.dumps({
        "new_title": "雨夜追杀",
        "new_summary": "主角在雨夜登场即遭追杀,仓促逃亡中暴露身手,引出追杀者背后的组织。",
        "change_reason": "原简述只有登场没有冲突,开场张力不足",
    }, ensure_ascii=False)
    adapter = _ChatAdapter(reply="你说的不对,是这章缺冲突,还是和上一章衔接断了?", distilled=distilled)
    with patch.object(od_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/outlines/1/discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "这章感觉不对劲"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "冲突" in body["reply"]
    proposal = body["proposal"]
    assert proposal is not None
    assert proposal["new_title"] == "雨夜追杀"
    assert "追杀" in proposal["new_summary"]
    assert "张力" in proposal["change_reason"]


def test_outline_discuss_no_proposal_when_dash(client):
    headers = _auth(client, "ol_disc_dash")
    p = _create_project(client, headers)
    _seed_outline(p["id"])

    from app.engines import outline_discuss as od_mod

    adapter = _ChatAdapter(reply="你具体觉得哪里不合理呢?", distilled="-")
    with patch.object(od_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/outlines/1/discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "说不好"}]},
        )
    assert r.status_code == 200, r.text
    assert r.json()["proposal"] is None


def test_outline_discuss_distill_failure_keeps_reply(client):
    """蒸馏调用抛错:不阻塞对话,reply 正常返回,proposal 置空。"""
    headers = _auth(client, "ol_disc_fail")
    p = _create_project(client, headers)
    _seed_outline(p["id"])

    from app.engines import outline_discuss as od_mod

    adapter = _ChatAdapter(reply="先把焦点放在开头这段?", distilled=RuntimeError("蒸馏炸了"))
    with patch.object(od_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/outlines/1/discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "开头不行"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "开头" in body["reply"]
    assert body["proposal"] is None


def test_outline_discuss_empty_messages_400(client):
    headers = _auth(client, "ol_disc_empty")
    p = _create_project(client, headers)
    _seed_outline(p["id"])
    r = client.post(
        f"/api/projects/{p['id']}/outlines/1/discuss",
        headers=headers,
        json={"messages": []},
    )
    assert r.status_code == 400


def test_outline_discuss_last_not_user_400(client):
    headers = _auth(client, "ol_disc_last")
    p = _create_project(client, headers)
    _seed_outline(p["id"])
    r = client.post(
        f"/api/projects/{p['id']}/outlines/1/discuss",
        headers=headers,
        json={"messages": [
            {"role": "user", "content": "这章不对"},
            {"role": "assistant", "content": "哪里不对?"},
        ]},
    )
    assert r.status_code == 400


def test_outline_discuss_no_outline_404(client):
    headers = _auth(client, "ol_disc_nooutline")
    p = _create_project(client, headers)
    r = client.post(
        f"/api/projects/{p['id']}/outlines/9/discuss",
        headers=headers,
        json={"messages": [{"role": "user", "content": "在?"}]},
    )
    assert r.status_code == 404


def test_outline_discuss_not_owner_404(client):
    a = _auth(client, "ol_disc_a")
    b = _auth(client, "ol_disc_b")
    p = _create_project(client, a, "别人的书")
    _seed_outline(p["id"])
    r = client.post(
        f"/api/projects/{p['id']}/outlines/1/discuss",
        headers=b,
        json={"messages": [{"role": "user", "content": "在?"}]},
    )
    assert r.status_code == 404
