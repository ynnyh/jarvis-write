# tests/test_embedding_settings.py
# -*- coding: utf-8 -*-
"""embedding 专用配置:解析优先级(DB > env > 默认 provider)+ 设置页 embedding 卡。

伪 provider "embedding" 复用 provider_settings 表,但不进 LLM 适配器注册表。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    # 进入上下文才会跑 lifespan(建表 + 幂等迁移),全程在临时库上
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str, password: str = "pass123") -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _with_uid(client: TestClient, headers: dict, fn):
    """在指定用户的 contextvar 下调用函数(模拟请求上下文)。"""
    from app.auth import current_user_id

    me = client.get("/api/auth/me", headers=headers).json()
    tok = current_user_id.set(me["id"])
    try:
        return fn()
    finally:
        current_user_id.reset(tok)


def _setup_deepseek_key(client: TestClient, username: str) -> dict:
    """注册并给 deepseek 存一个假 key(走数据库,不碰 .env)。"""
    headers = _auth(_register(client, username)["token"])
    r = client.put(
        "/api/settings/providers/deepseek",
        headers=headers,
        json={
            "api_key": "sk-deep",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "is_default": True,
        },
    )
    assert r.status_code == 200, r.text
    return headers


def _put_embedding(client: TestClient, headers: dict, **kw) -> dict:
    body = {"api_key": None, "base_url": "", "model": ""}
    body.update(kw)
    r = client.put("/api/settings/providers/embedding", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- resolve_embedding_config 优先级 ----------


def test_resolve_unconfigured_falls_back_to_default(client):
    """什么都没配 → source=default,且默认 provider 也没 key(api_key 为空)。"""
    from app.llm.embeddings import resolve_embedding_config

    headers = _auth(_register(client, "emb_resolve_none")["token"])
    cfg = _with_uid(client, headers, resolve_embedding_config)
    assert cfg["source"] == "default"
    assert cfg["api_key"] == ""


def test_resolve_default_uses_default_provider(client):
    """只配了聊天 provider → 走默认 provider 的 base_url+key,模型取 settings。"""
    from app.llm.embeddings import resolve_embedding_config

    headers = _setup_deepseek_key(client, "emb_resolve_default")
    cfg = _with_uid(client, headers, resolve_embedding_config)
    assert cfg["source"] == "default"
    assert cfg["api_key"] == "sk-deep"
    assert cfg["base_url"] == "https://api.deepseek.com"
    assert cfg["model"] == get_settings().embedding_model


def test_resolve_env_beats_default(client, monkeypatch):
    """env 专用配置优先于默认 provider 兜底。"""
    from app.llm.embeddings import resolve_embedding_config

    monkeypatch.setattr(get_settings(), "embedding_api_key", "sk-env-emb")
    monkeypatch.setattr(
        get_settings(), "embedding_base_url", "https://api.siliconflow.cn/v1/"
    )

    headers = _setup_deepseek_key(client, "emb_resolve_env")
    cfg = _with_uid(client, headers, resolve_embedding_config)
    assert cfg["source"] == "env"
    assert cfg["api_key"] == "sk-env-emb"
    # base_url 尾斜杠被剥掉
    assert cfg["base_url"] == "https://api.siliconflow.cn/v1"
    assert cfg["model"] == get_settings().embedding_model


def test_resolve_db_beats_env(client, monkeypatch):
    """设置页保存的 embedding 行(有 key)优先级最高。"""
    from app.llm.embeddings import resolve_embedding_config

    monkeypatch.setattr(get_settings(), "embedding_api_key", "sk-env-emb")
    monkeypatch.setattr(
        get_settings(), "embedding_base_url", "https://api.siliconflow.cn/v1"
    )

    headers = _setup_deepseek_key(client, "emb_resolve_db")
    _put_embedding(
        client,
        headers,
        api_key="sk-user-emb",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="embedding-3",
    )
    cfg = _with_uid(client, headers, resolve_embedding_config)
    assert cfg["source"] == "user"
    assert cfg["api_key"] == "sk-user-emb"
    assert cfg["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert cfg["model"] == "embedding-3"


def test_resolve_db_row_without_key_ignored(client):
    """DB 里有 embedding 行但没 key(被清过)→ 不算,回落到 default。"""
    from app.llm.embeddings import resolve_embedding_config

    headers = _setup_deepseek_key(client, "emb_resolve_nokey")
    # 先存 key 再用纯空白清除
    _put_embedding(client, headers, api_key="sk-user-emb", base_url="https://x/v1")
    _put_embedding(client, headers, api_key="   ", base_url="https://x/v1")

    cfg = _with_uid(client, headers, resolve_embedding_config)
    assert cfg["source"] == "default"
    assert cfg["api_key"] == "sk-deep"


# ---------- GET /providers 末尾的 embedding 卡 ----------


def _embedding_card(client: TestClient, headers: dict) -> dict:
    r = client.get("/api/settings/providers", headers=headers)
    assert r.status_code == 200, r.text
    cards = r.json()
    assert cards[-1]["provider"] == "embedding"
    assert [c["provider"] for c in cards[:-1]] == ["deepseek", "openai", "gemini"]
    return cards[-1]


def test_embedding_card_unconfigured(client):
    """全新用户:卡存在,is_default 恒 false,source=none(未配置)。"""
    headers = _auth(_register(client, "emb_card_none")["token"])
    card = _embedding_card(client, headers)
    assert card["is_default"] is False
    assert card["has_key"] is False
    assert card["source"] == "none"
    # 聊天卡的 source 字段恒为 ""
    r = client.get("/api/settings/providers", headers=headers)
    assert all(c["source"] == "" for c in r.json()[:-1])


def test_embedding_card_source_default(client):
    """只配聊天 provider → embedding 卡 source=default。"""
    headers = _setup_deepseek_key(client, "emb_card_default")
    assert _embedding_card(client, headers)["source"] == "default"


def test_embedding_card_after_save(client):
    """保存专用配置后:has_key + 打码 + source=user,is_default 不被带偏。"""
    headers = _setup_deepseek_key(client, "emb_card_saved")
    out = _put_embedding(
        client,
        headers,
        api_key="sk-emb-secret-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        is_default=True,  # 应被忽略
    )
    assert out["provider"] == "embedding"
    assert out["has_key"] is True
    assert out["is_default"] is False
    assert out["source"] == "user"
    assert "*" in out["api_key_masked"]
    assert "sk-emb-secret-key" not in out["api_key_masked"]

    # 不影响 deepseek 的默认地位
    r = client.get("/api/settings/providers", headers=headers)
    deepseek = [c for c in r.json() if c["provider"] == "deepseek"][0]
    assert deepseek["is_default"] is True


def test_put_embedding_empty_key_keeps_saved(client):
    """api_key 留空 = 不修改已存 key(与聊天卡同一语义)。"""
    headers = _setup_deepseek_key(client, "emb_put_keep")
    _put_embedding(client, headers, api_key="sk-emb-keep-me")
    out = _put_embedding(client, headers, api_key="", base_url="https://new/v1")
    assert out["has_key"] is True
    assert out["base_url"] == "https://new/v1"
    assert out["source"] == "user"


def test_embedding_card_requires_auth(client):
    assert client.get("/api/settings/providers").status_code == 401


# ---------- POST /providers/embedding/test ----------


def test_embedding_test_ok(client):
    """保存后测试:真实调用被 mock,返回 ok + model + source。"""
    headers = _setup_deepseek_key(client, "emb_test_ok")
    _put_embedding(
        client,
        headers,
        api_key="sk-emb-test",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
    )
    with patch(
        "app.llm.embeddings.EmbeddingClient.embed",
        new=AsyncMock(return_value=[[0.1, 0.2]]),
    ):
        r = client.post("/api/settings/providers/embedding/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "BAAI/bge-m3"
    assert body["source"] == "user"
    assert body["error"] == ""


def test_embedding_test_no_key(client):
    """完全未配置 → ok=false,source=none,带原因。"""
    headers = _auth(_register(client, "emb_test_none")["token"])
    r = client.post("/api/settings/providers/embedding/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["source"] == "none"
    assert "api_key" in body["error"]


def test_embedding_test_failure(client):
    """embed 抛错 → ok=false 且带原因,不抛 500。"""
    headers = _setup_deepseek_key(client, "emb_test_fail")
    _put_embedding(
        client,
        headers,
        api_key="sk-emb-bad",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
    )
    with patch(
        "app.llm.embeddings.EmbeddingClient.embed",
        new=AsyncMock(side_effect=RuntimeError("401 unauthorized")),
    ):
        r = client.post("/api/settings/providers/embedding/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["source"] == "user"
    assert "401" in body["error"]
