# backend/tests/test_provider_configs.py
# -*- coding: utf-8 -*-
"""cc-switch 风格多配置:CRUD / 默认与快档唯一性 / 档位解析 / 老表迁移。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> tuple[dict, int]:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    uid = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, uid


def _with_uid(client: TestClient, headers: dict, fn):
    from app.auth import current_user_id

    me = client.get("/api/auth/me", headers=headers).json()
    tok = current_user_id.set(me["id"])
    try:
        return fn()
    finally:
        current_user_id.reset(tok)


def _create(client, headers, **kw):
    body = {"interface_format": "openai", "api_key": "sk-x", **kw}
    r = client.post("/api/settings/providers", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- CRUD 与默认/快档唯一性 ----------

def test_first_config_auto_default(client):
    """首套配置自动成为默认(quality 档)。"""
    headers, _ = _auth(client, "cfg_first")
    c = _create(client, headers, name="主力")
    assert c["is_default"] is True
    assert c["is_default_fast"] is False
    assert c["has_key"] is True

    lst = client.get("/api/settings/providers", headers=headers).json()
    assert len(lst) == 1
    assert lst[0]["name"] == "主力"


def test_default_and_fast_flags_are_unique(client):
    """设默认/快档会清掉其他配置的同名标记。"""
    headers, _ = _auth(client, "cfg_flags")
    a = _create(client, headers, name="A")
    b = _create(client, headers, name="B", interface_format="deepseek")

    r = client.put(
        f"/api/settings/providers/{b['id']}",
        headers=headers,
        json={
            "interface_format": "deepseek",
            "is_default": True,
            "is_default_fast": True,
        },
    )
    assert r.status_code == 200, r.text

    lst = {c["id"]: c for c in client.get(
        "/api/settings/providers", headers=headers).json()}
    assert lst[b["id"]]["is_default"] is True
    assert lst[b["id"]]["is_default_fast"] is True
    assert lst[a["id"]]["is_default"] is False
    assert lst[a["id"]]["is_default_fast"] is False


def test_update_keeps_key_when_blank_and_sets_overrides(client):
    """更新不传 key 不清空;timeout/max_tokens 覆盖随配置保存。"""
    headers, _ = _auth(client, "cfg_update")
    c = _create(client, headers, api_key="sk-keep-me")
    r = client.put(
        f"/api/settings/providers/{c['id']}",
        headers=headers,
        json={
            "interface_format": "openai",
            "name": "改名",
            "base_url": "https://relay.example.com/v1",
            "model": "deepseek-v4-pro",
            "timeout": 300,
            "max_tokens": 16384,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "改名"
    assert body["timeout"] == 300
    assert body["max_tokens"] == 16384
    assert body["has_key"] is True  # key 未被清空
    assert "sk-keep-me" not in body["api_key_masked"]


def test_delete_unreachable_config_directly(client):
    """假 key 探测不通 → 直接删除,无需二次确认。"""
    headers, _ = _auth(client, "cfg_delete")
    c = _create(client, headers)
    r = client.delete(
        f"/api/settings/providers/{c['id']}", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert client.get("/api/settings/providers", headers=headers).json() == []


def test_configs_are_per_user(client):
    """A 的配置对 B 不可见也不可操作。"""
    ha, _ = _auth(client, "cfg_iso_a")
    hb, _ = _auth(client, "cfg_iso_b")
    c = _create(client, ha, name="A 的配置")

    assert client.get("/api/settings/providers", headers=hb).json() == []
    r = client.put(
        f"/api/settings/providers/{c['id']}",
        headers=hb,
        json={"interface_format": "openai"},
    )
    assert r.status_code == 404
    r = client.delete(f"/api/settings/providers/{c['id']}", headers=hb)
    assert r.status_code == 404


# ---------- 档位解析 ----------

def test_fast_tier_uses_fast_default_then_falls_back(client):
    """fast 档:有快档标记用快档;没有则跟随 quality 档配置。"""
    from app.llm.factory import resolve_tier_config

    headers, _ = _auth(client, "cfg_tier")
    q = _create(client, headers, name="强模型", model="gpt-strong")
    assert _with_uid(client, headers, resolve_tier_config)["id"] == q["id"]
    # 未设快档 → fast 跟随 quality
    fast = _with_uid(client, headers, lambda: resolve_tier_config("fast"))
    assert fast["id"] == q["id"]

    f = _create(client, headers, name="快模型", model="gpt-cheap")
    client.put(
        f"/api/settings/providers/{f['id']}",
        headers=headers,
        json={"interface_format": "openai", "is_default_fast": True},
    )
    fast = _with_uid(client, headers, lambda: resolve_tier_config("fast"))
    assert fast["id"] == f["id"]
    # quality 不受影响
    assert _with_uid(client, headers, resolve_tier_config)["id"] == q["id"]


def test_adapter_uses_config_overrides(client):
    """config_id 造适配器:用配置里的 model/timeout/max_tokens。"""
    from app.llm.factory import create_llm_adapter

    headers, _ = _auth(client, "cfg_adapter")
    c = _create(
        client, headers, model="m-x", base_url="https://r.example.com/v1",
        timeout=300, max_tokens=16384,
    )
    adapter = _with_uid(
        client, headers, lambda: create_llm_adapter(config_id=c["id"])
    )
    assert adapter.model_name == "m-x"
    assert adapter.timeout == 300
    assert adapter.max_tokens == 16384
    # 显式覆盖优先于配置
    adapter2 = _with_uid(
        client, headers,
        lambda: create_llm_adapter(config_id=c["id"], max_tokens=100),
    )
    assert adapter2.max_tokens == 100


def test_default_config_wins_over_earliest_of_same_protocol(client):
    """同协议多套配置:生效的必须是标了「默认」的那套,不是创建最早的那套。

    这是线上 403 的形状,也是先前所有档位测试都没覆盖的那一格(它们的默认配置
    恰好就是第一套,取最早/取默认结果一样,分叉看不出来)。老代码把「默认档的
    协议名」喂给按协议名取配置的旧接口,于是永远打到该协议里**创建最早**的那套
    中转站——用户在设置页把默认换成官方 DeepSeek 也没用,报错还是那个中转站的
    Cloudflare 挑战页(HTTP 403 "Just a moment...")。所以这里专门把默认那套排在
    后面,再把三条入口逐个钉住。
    """
    from app.llm.factory import create_llm_adapter, resolve_tier_config
    from app.llm.router import Task, get_adapter_for

    headers, _ = _auth(client, "cfg_default_wins")
    relay = _create(
        client, headers, name="中转站", interface_format="openai-compatible",
        base_url="https://relay.example.com", model="relay-model",
    )
    official = _create(
        client, headers, name="官方", interface_format="openai-compatible",
        base_url="https://api.official.example.com/v1", model="official-model",
    )
    assert relay["id"] < official["id"]  # 先建的是中转站(自动成了默认)
    # PUT 是整体替换(base_url 省略即清空),所以带全字段
    r = client.put(
        f"/api/settings/providers/{official['id']}",
        headers=headers,
        json={
            "interface_format": "openai-compatible",
            "base_url": "https://api.official.example.com/v1",
            "model": "official-model",
            "is_default": True,
        },
    )
    assert r.status_code == 200, r.text

    assert _with_uid(client, headers, resolve_tier_config)["id"] == official["id"]
    # 无参 create_llm_adapter 与任务路由 get_adapter_for 都必须落在默认那套上
    for make in (create_llm_adapter, lambda: get_adapter_for(Task.TITLE)):
        adapter = _with_uid(client, headers, make)
        assert adapter.base_url == "https://api.official.example.com/v1"
        assert adapter.model_name == "official-model"
    # 反向钉一句:按协议名取的是最早那套(旧接口的语义没变),
    # 所以它不能被用来造「当前默认」的适配器——这正是那个 403 的成因。
    legacy = _with_uid(client, headers, lambda: create_llm_adapter("openai-compatible"))
    assert legacy.base_url == "https://relay.example.com"


# ---------- 老表迁移 ----------

def test_migrate_old_provider_settings_to_configs(client):
    """provider_settings 老行 → provider_configs;幂等;is_default 保留。"""
    from app.db.models import ProviderConfig, ProviderSetting
    from app.db.session import SessionLocal
    from app.migrate import _migrate_provider_settings_to_configs

    db = SessionLocal()
    try:
        db.add(ProviderSetting(
            user_id=88881, provider="deepseek", api_key="enc-key",
            base_url="https://api.deepseek.com", model="deepseek-chat",
            is_default=True,
        ))
        db.add(ProviderSetting(
            user_id=88881, provider="openai", api_key="",
            base_url="", model="", is_default=False,
        ))
        db.commit()
    finally:
        db.close()

    _migrate_provider_settings_to_configs()
    _migrate_provider_settings_to_configs()  # 幂等:再跑不重复

    db = SessionLocal()
    try:
        rows = (
            db.query(ProviderConfig)
            .filter(ProviderConfig.user_id == 88881)
            .order_by(ProviderConfig.id)
            .all()
        )
        assert len(rows) == 2
        deep = next(r for r in rows if r.interface_format == "deepseek")
        assert deep.name == "DeepSeek"
        assert deep.api_key == "enc-key"  # 原样拷贝(已是密文或历史明文)
        assert deep.is_default is True
        assert deep.is_default_fast is False
    finally:
        db.close()
