# tests/test_writing_cards.py
# -*- coding: utf-8 -*-
"""写作手法卡:渲染纯函数 + CRUD 接口 + 归属隔离 + 项目删除级联 + 注入回归。"""
import pytest
from fastapi.testclient import TestClient

from app.engines.tendency.cards import MAX_BODY_CHARS, MAX_CARDS, render_cards_block
from app.main import app

INVITE = "test-invite"


class FakeCard:
    """鸭子类型的假卡(渲染层只按属性取值,不依赖 ORM)。"""

    def __init__(self, title="手法", body="正文", enabled=True, sort=0):
        self.title = title
        self.body = body
        self.enabled = enabled
        self.sort = sort


# ---------- 渲染纯函数 ----------


def test_render_empty_and_all_disabled():
    """无卡 / 全禁用 / body 空白 → 空串(注入块整体省略,不留空标题)。"""
    assert render_cards_block(None) == ""
    assert render_cards_block([]) == ""
    assert render_cards_block([FakeCard(enabled=False)]) == ""
    assert render_cards_block([FakeCard(body="   ")]) == ""


def test_render_enabled_sorted_and_filtered():
    """只渲染启用卡,按 sort 升序编号;禁用卡不出现在块里。"""
    block = render_cards_block(
        [
            FakeCard(title="后", body="后置手法", sort=20),
            FakeCard(title="禁用", body="不该出现", enabled=False, sort=1),
            FakeCard(title="前", body="前置手法", sort=10),
        ]
    )
    assert "【写作手法卡" in block
    assert "1. 前:前置手法" in block
    assert "2. 后:后置手法" in block
    assert "不该出现" not in block
    assert block.index("前置手法") < block.index("后置手法")


def test_render_truncates_body_and_caps_count():
    """单卡正文超限截断,启用卡超上限只取前 MAX_CARDS 张。"""
    block = render_cards_block([FakeCard(body="长" * (MAX_BODY_CHARS + 50))])
    assert "……" in block
    assert "长" * (MAX_BODY_CHARS + 1) not in block

    many = [FakeCard(title=f"卡{i}", body=f"手法{i}", sort=i) for i in range(MAX_CARDS + 5)]
    block = render_cards_block(many)
    assert f"手法{MAX_CARDS - 1}" in block
    assert f"手法{MAX_CARDS + 4}" not in block


# ---------- CRUD 接口 ----------


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project(client: TestClient, headers: dict, title="手法卡测试书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def test_card_crud_roundtrip(client):
    """新建 → 列表 → 切启用 → 预览注入块 → 删除。"""
    headers = _auth(_register(client, "cards_owner")["token"])
    pid = _project(client, headers)["id"]

    assert client.get(f"/api/projects/{pid}/cards", headers=headers).json() == []

    r = client.post(
        f"/api/projects/{pid}/cards",
        headers=headers,
        json={"title": "冷峻硬汉对峙", "body": "短句、不写心理活动、动作代替情绪"},
    )
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["enabled"] is False and card["sort"] > 0

    listed = client.get(f"/api/projects/{pid}/cards", headers=headers).json()
    assert [c["id"] for c in listed] == [card["id"]]

    # 只传 enabled 的局部更新:标题正文保持不变
    r = client.patch(
        f"/api/projects/{pid}/cards/{card['id']}", headers=headers, json={"enabled": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert r.json()["title"] == "冷峻硬汉对峙"

    r = client.get(f"/api/projects/{pid}/cards/preview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled_count"] == 1
    assert "冷峻硬汉对峙" in body["block"]

    assert client.delete(
        f"/api/projects/{pid}/cards/{card['id']}", headers=headers
    ).status_code == 200
    assert client.get(f"/api/projects/{pid}/cards", headers=headers).json() == []


def test_card_rejects_overlong_body(client):
    headers = _auth(_register(client, "cards_limits")["token"])
    pid = _project(client, headers)["id"]
    r = client.post(
        f"/api/projects/{pid}/cards",
        headers=headers,
        json={"title": "超长", "body": "字" * (MAX_BODY_CHARS + 1)},
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/projects/{pid}/cards", headers=headers, json={"title": "", "body": "x"}
    )
    assert r.status_code == 422


def test_card_cross_project_isolation(client):
    """别人书里的卡 id 一律 404(读改删都不泄露存在性)。"""
    a = _auth(_register(client, "cards_a")["token"])
    b = _auth(_register(client, "cards_b")["token"])
    pid_a = _project(client, a, "A的书")["id"]
    pid_b = _project(client, b, "B的书")["id"]
    card = client.post(
        f"/api/projects/{pid_a}/cards", headers=a, json={"title": "A卡", "body": "A手法"}
    ).json()

    # B 拿自己项目 + A 的卡 id
    assert client.patch(
        f"/api/projects/{pid_b}/cards/{card['id']}", headers=b, json={"enabled": True}
    ).status_code == 404
    assert client.delete(
        f"/api/projects/{pid_b}/cards/{card['id']}", headers=b
    ).status_code == 404
    # B 直接访问 A 的项目 → 项目层就 404
    assert client.get(f"/api/projects/{pid_a}/cards", headers=b).status_code == 404
    # A 的卡完好
    assert len(client.get(f"/api/projects/{pid_a}/cards", headers=a).json()) == 1


def test_delete_project_clears_cards(client):
    """删项目后手法卡同步清空(级联元组已含 WritingCard)。"""
    from app.db.models import WritingCard
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "cards_cascade")["token"])
    pid = _project(client, headers, "要删的书")["id"]
    client.post(
        f"/api/projects/{pid}/cards", headers=headers, json={"title": "卡", "body": "手法"}
    )

    assert client.delete(f"/api/projects/{pid}", headers=headers).status_code == 200
    db = SessionLocal()
    try:
        assert db.query(WritingCard).filter_by(project_id=pid).count() == 0
    finally:
        db.close()


# ---------- 注入回归(mock LLM,不需要 key) ----------


def test_polish_prompt_carries_enabled_cards():
    """启用卡的正文进入润色 prompt,禁用卡不进(走 style_directives 通道)。"""
    import asyncio
    from unittest.mock import patch

    from app.engines.polish import polisher

    class _Adapter:
        def __init__(self):
            self.prompts: list[str] = []

        async def ask(self, prompt: str, system: str | None = None) -> str:
            self.prompts.append(prompt)
            if "抽取" in prompt and "事实" in prompt:
                return '{"facts": []}'
            return "【润色稿】他推门进来。"

    adapter = _Adapter()
    cards = [
        FakeCard(title="冷峻硬汉对峙", body="短句、动作代替情绪", enabled=True, sort=0),
        FakeCard(title="停用卡", body="绝对不该注入的文本", enabled=False, sort=1),
    ]
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        asyncio.run(polisher.polish_text("他走了进来。", cards=cards))

    prompt = next(p for p in adapter.prompts if "待润色文本" in p)
    assert "【写作手法卡" in prompt
    assert "短句、动作代替情绪" in prompt
    assert "绝对不该注入的文本" not in prompt
