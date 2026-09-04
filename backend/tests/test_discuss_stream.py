# tests/test_discuss_stream.py
# -*- coding: utf-8 -*-
"""AI 对话真流式(SSE 打字机)测试:discuss-stream / revise-discuss-stream。

验证点:
- 逐字 token 帧拼出 reply;done 帧给结构化收尾(chat=suggestion,revise=directive+档位)
- chat:【改写建议】标记跨 chunk 也能正确切分——改写正文不会漏进 token 帧(聊天气泡)
- 无【改写建议】时 token 拼出全文,done.suggestion 为 None
- 缺章 → HTTP 404(流式开始前);LLM 阶段错误(空对话)→ 200 + SSE error 帧
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


def _create_project(client: TestClient, headers: dict, title: str = "流式对话书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_chapter(project_id: int, content: str = "他走进了城门。") -> None:
    db = SessionLocal()
    try:
        outline = Outline(
            project_id=project_id, chapter_number=1, title="第一章",
            summary="主角进城", current_version=1,
        )
        db.add(outline)
        db.flush()
        db.add(Chapter(
            project_id=project_id, outline_id=outline.id, chapter_number=1,
            final_content=content, status="approved", word_count=len(content),
        ))
        db.commit()
    finally:
        db.close()


def _parse_sse(text: str) -> list[tuple[str, object]]:
    """把 text/event-stream 响应体拆成 [(event, data), ...];data 走 JSON 反序列化。"""
    events: list[tuple[str, object]] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        ev = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        data = json.loads("\n".join(data_lines)) if data_lines else None
        events.append((ev, data))
    return events


class _StreamAdapter:
    """假流式适配器:stream() 按预设分块逐块吐;ask() 返回蒸馏结果(重写研讨两段式用)。"""

    def __init__(self, chunks: list[str], distilled: str = "-"):
        self._chunks = chunks
        self._distilled = distilled
        self.max_tokens = 8192

    def _record_usage(self, resp):  # noqa: ANN001
        pass

    async def stream(self, messages):
        for c in self._chunks:
            yield c

    async def ask(self, prompt, system=None):
        return self._distilled


# ---------------- chat:discuss-stream ----------------


def test_discuss_stream_tokens_then_suggestion(client):
    """标记【改写建议】被拆在多个 chunk 之间时:token 只拼出 reply,改写正文归 done.suggestion。"""
    headers = _auth(client, "stream_disc_sugg")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    # 故意把 mark「【改写建议】」拆断,考验尾缓冲防漏逻辑
    chunks = ["我把它写得更紧张一些。\n【改", "写建", "议】\n他屏住呼吸,", "一步跨进了城门。"]
    adapter = _StreamAdapter(chunks)
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss-stream",
            headers=headers,
            json={"messages": [{"role": "user", "content": "帮我改紧张点"}],
                  "target": "他走进了城门。"},
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    tokens = "".join(e[1]["text"] for e in events if e[0] == "token")
    done = [e[1] for e in events if e[0] == "done"]
    # token 只含 reply,绝不能漏出改写正文(否则聊天气泡里会先闪一下改写内容)
    assert "他屏住呼吸" not in tokens
    assert "我把它写得更紧张一些" in tokens
    assert len(done) == 1
    assert done[0]["reply"] == "我把它写得更紧张一些。"
    assert done[0]["suggestion"] == "他屏住呼吸,一步跨进了城门。"


def test_discuss_stream_no_suggestion(client):
    """纯解释(无【改写建议】):token 拼出全文,done.suggestion 为 None。"""
    headers = _auth(client, "stream_disc_plain")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    adapter = _StreamAdapter(["这段是说主角", "抵达了城门口", ",情绪忐忑。"])
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss-stream",
            headers=headers,
            json={"messages": [{"role": "user", "content": "这段啥意思"}],
                  "target": "他走进了城门。"},
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    tokens = "".join(e[1]["text"] for e in events if e[0] == "token")
    done = [e[1] for e in events if e[0] == "done"]
    assert tokens == "这段是说主角抵达了城门口,情绪忐忑。"
    assert len(done) == 1
    assert done[0]["reply"] == "这段是说主角抵达了城门口,情绪忐忑。"
    assert done[0]["suggestion"] is None


def test_discuss_stream_missing_chapter_404(client):
    """缺章:流式开始前走正常 HTTP 404,不进 SSE。"""
    headers = _auth(client, "stream_disc_404")
    p = _create_project(client, headers)  # 不塞章节
    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/discuss-stream",
        headers=headers,
        json={"messages": [{"role": "user", "content": "在?"}], "target": ""},
    )
    assert r.status_code == 404


def test_discuss_stream_empty_messages_error_frame(client):
    """空对话:LLM 阶段的校验错误走 SSE error 帧(HTTP 已 200)。"""
    headers = _auth(client, "stream_disc_empty")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])
    adapter = _StreamAdapter(["不该被用到"])
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss-stream",
            headers=headers,
            json={"messages": [], "target": "他走进了城门。"},
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert any(e[0] == "error" and "请先说点什么" in e[1]["detail"] for e in events)
    assert not any(e[0] == "done" for e in events)


# ---------------- revise:revise-discuss-stream ----------------


def test_revise_discuss_stream_tokens_then_directive(client):
    """revise 流式:token 拼出 reply;done 给蒸馏出的 directive + 档位建议。"""
    headers = _auth(client, "stream_revise_ok")
    p = _create_project(client, headers)
    _seed_chapter(p["id"])

    from app.engines.pipeline import chapter as ch_mod
    from app.engines.pipeline import chapter_maintenance as cm_mod
    from app.engines.pipeline import rewrite_session as rs_mod

    adapter = _StreamAdapter(
        chunks=["你说节奏拖,", "是开头铺垫太长?"],
        distilled='{"directive": "1. 开头砍一半", "level": "polish"}',
    )
    with patch.object(rs_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-discuss-stream",
            headers=headers,
            json={"messages": [{"role": "user", "content": "这章节奏太拖了"}]},
        )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    tokens = "".join(e[1]["text"] for e in events if e[0] == "token")
    done = [e[1] for e in events if e[0] == "done"]
    assert tokens == "你说节奏拖,是开头铺垫太长?"
    assert len(done) == 1
    assert done[0]["reply"] == "你说节奏拖,是开头铺垫太长?"
    assert "开头砍一半" in done[0]["directive"]
    assert done[0]["suggested_level"] == "polish"


def test_revise_discuss_stream_no_content_404(client):
    """章节尚无定稿正文:流式开始前走 HTTP 404。"""
    headers = _auth(client, "stream_revise_404")
    p = _create_project(client, headers)
    db = SessionLocal()
    try:
        db.add(Outline(project_id=p["id"], chapter_number=1, title="空章", current_version=1))
        db.commit()
    finally:
        db.close()
    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-discuss-stream",
        headers=headers,
        json={"messages": [{"role": "user", "content": "改改"}]},
    )
    assert r.status_code == 404
