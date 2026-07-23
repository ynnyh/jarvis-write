# tests/test_revise_discuss.py
# -*- coding: utf-8 -*-
"""重写研讨(对话式)测试:聊清"这章哪里不满意" → 蒸馏修改意见 → 回填重写。

验证点:
- POST .../chapters/{n}/revise-discuss 返回 reply + directive(蒸馏结果)
- 蒸馏出"-"(无明确意见)归一化成空串
- 空对话 → 400;无正文的章 → 404;对他人项目 → 404
- 引擎级:distill 失败不阻塞对话(reply 正常返回,directive 置空)
"""
from __future__ import annotations

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


def _create_project(client: TestClient, headers: dict, title: str = "重写研讨书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_chapter(project_id: int, n: int = 1, content: str = "这是第一章的正文。" * 20) -> None:
    """直接往库里塞一章蓝图 + 定稿正文(重写研讨要求章节已有正文)。"""
    db = SessionLocal()
    try:
        db.add(Outline(
            project_id=project_id, chapter_number=n, title="雨夜",
            chapter_purpose="主角登场", summary="主角在雨夜登场",
            foreshadowing="埋下身世伏笔", current_version=1,
        ))
        db.add(Chapter(
            project_id=project_id, chapter_number=n,
            final_content=content, status="finalized", word_count=len(content),
        ))
        db.commit()
    finally:
        db.close()


class _ChatAdapter:
    """假适配器:complete 返回续聊回复,ask 返回蒸馏结果(重写研讨两段式)。"""

    def __init__(self, reply: str, distilled: str):
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


def test_revise_discuss_returns_reply_and_directive(client):
    headers = _auth(client, "revise_disc_user")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    from app.engines.pipeline import chapter as ch_mod

    adapter = _ChatAdapter(
        reply="你说节奏拖,是开头铺垫太长,还是中间对话太水?",
        distilled="1. 开头铺垫砍掉一半,直接进冲突\n2. 删掉中段重复的内心戏",
    )
    with patch.object(ch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "这章节奏太拖了"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "铺垫" in body["reply"]
    assert "开头铺垫砍掉一半" in body["directive"]


def test_revise_discuss_empty_directive_when_dash(client):
    headers = _auth(client, "revise_disc_dash")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    from app.engines.pipeline import chapter as ch_mod

    adapter = _ChatAdapter(reply="你具体是指哪里不满意呢?", distilled="-")
    with patch.object(ch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "不太满意"}]},
        )
    assert r.status_code == 200, r.text
    assert r.json()["directive"] == ""


def test_revise_discuss_distill_failure_keeps_reply(client):
    """蒸馏调用抛错:不阻塞对话,reply 正常返回,directive 置空。"""
    headers = _auth(client, "revise_disc_fail")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    from app.engines.pipeline import chapter as ch_mod

    adapter = _ChatAdapter(reply="我们先聚焦开头怎么样?", distilled=RuntimeError("蒸馏炸了"))
    with patch.object(ch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "开头不行"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "开头" in body["reply"]
    assert body["directive"] == ""


def test_revise_discuss_empty_messages_400(client):
    headers = _auth(client, "revise_disc_empty")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])
    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-discuss",
        headers=headers,
        json={"messages": []},
    )
    assert r.status_code == 400


def test_revise_discuss_no_content_404(client):
    """章节尚无定稿正文 → 404(没东西可重写)。"""
    headers = _auth(client, "revise_disc_nocontent")
    p = _create_project(client, headers)
    # 只塞蓝图,不塞正文
    db = SessionLocal()
    try:
        db.add(Outline(project_id=p["id"], chapter_number=1, title="空章", current_version=1))
        db.commit()
    finally:
        db.close()
    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-discuss",
        headers=headers,
        json={"messages": [{"role": "user", "content": "改改"}]},
    )
    assert r.status_code == 404


def test_revise_discuss_not_owner_404(client):
    a = _auth(client, "revise_disc_a")
    b = _auth(client, "revise_disc_b")
    p = _create_project(client, a, "别人的书")
    _seed_chapter(p["id"])
    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-discuss",
        headers=b,
        json={"messages": [{"role": "user", "content": "在?"}]},
    )
    assert r.status_code == 404
