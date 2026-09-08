# tests/test_usage_pricing.py
"""价格折算与峰时判断、用量 API 金额字段、余额端点分支。

守的是三条规矩:
- 没把握的价格绝不编(未知模型 estimated_cost 必须是 None);
- 金额是「未命中底价」上界估算,口径要写进响应(note);
- 余额查询只对 deepseek 协议开放,且必须过归属校验。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.llm import pricing
from app.main import app

INVITE = "test-invite"


# ---- 定价模块 ----


def test_flash_price_matches_by_prefix():
    p = pricing.match_price("deepseek-v4-flash")
    assert p is not None
    assert (p.cache_hit, p.input, p.output) == (0.02, 1.0, 2.0)
    assert p.peak_multiplier == 2.0


def test_unknown_model_has_no_price():
    """没有把握的价格一律 None——宁可不算钱,不编价。"""
    assert pricing.match_price("some-relay-model-x") is None
    assert pricing.match_price("") is None
    assert pricing.estimate_cost("some-relay-model-x", 1000, 1000) is None


def test_estimate_cost_math():
    # 1M 输入(未命中) + 1M 输出 = ¥1 + ¥2 = ¥3
    assert pricing.estimate_cost("deepseek-v4-flash", 1_000_000, 1_000_000) == 3.0
    # 峰时 ×2
    assert pricing.estimate_cost("deepseek-v4-flash", 1_000_000, 0, peak=True) == 2.0


def test_peak_hours_boundaries():
    # 北京时间 9:00 起峰,12:00 止;14:00 起峰,18:00 止
    tz8 = timezone(timedelta(hours=8))
    assert pricing.is_peak_now(datetime(2026, 9, 8, 9, 0, tzinfo=tz8)) is True
    assert pricing.is_peak_now(datetime(2026, 9, 8, 11, 59, tzinfo=tz8)) is True
    assert pricing.is_peak_now(datetime(2026, 9, 8, 12, 0, tzinfo=tz8)) is False
    assert pricing.is_peak_now(datetime(2026, 9, 8, 13, 59, tzinfo=tz8)) is False
    assert pricing.is_peak_now(datetime(2026, 9, 8, 14, 0, tzinfo=tz8)) is True
    assert pricing.is_peak_now(datetime(2026, 9, 8, 17, 59, tzinfo=tz8)) is True
    assert pricing.is_peak_now(datetime(2026, 9, 8, 18, 0, tzinfo=tz8)) is False
    assert pricing.is_peak_now(datetime(2026, 9, 8, 8, 59, tzinfo=tz8)) is False
    # UTC 输入也能正确折算:UTC 2:00 = 北京 10:00 = 峰时
    assert pricing.is_peak_now(datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)) is True


def test_peak_note_differs():
    """峰时/低峰文案不同,但都写明窗口。"""
    import app.llm.pricing as pmod

    orig = pmod.is_peak_now
    try:
        pmod.is_peak_now = lambda dt=None: True
        a = pmod.peak_note()
        pmod.is_peak_now = lambda dt=None: False
        b = pmod.peak_note()
    finally:
        pmod.is_peak_now = orig
    assert a != b
    assert pricing.PEAK_WINDOWS_TEXT in a and pricing.PEAK_WINDOWS_TEXT in b


# ---- /api/usage 金额字段 + 余额端点 ----


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def usage_env():
    """注册好的用户与预置 LLM 用量记录(conftest 已配好临时库)。"""
    from app.db.session import SessionLocal
    from app.db.models import LlmUsage

    with TestClient(app) as client:
        uname = f"usage_u_{datetime.now().strftime('%H%M%S%f')}"
        reg = _register(client, uname)
        uid = reg.get("user_id") or reg.get("id")
        if uid is None:  # 注册响应不带 id 时从 /me 取
            uid = client.get("/api/auth/me", headers=_auth(reg["token"])).json()["id"]
        with SessionLocal() as s:
            s.add(LlmUsage(user_id=uid, model="deepseek-v4-flash",
                           prompt_tokens=2_000_000, completion_tokens=500_000))
            s.add(LlmUsage(user_id=uid, model="mystery-relay-model",
                           prompt_tokens=100, completion_tokens=100))
            s.commit()
        yield client, reg["token"], uid


def test_usage_api_cost_fields(usage_env):
    client, token, _uid = usage_env
    r = client.get("/api/usage", headers=_auth(token))
    assert r.status_code == 200, r.text
    d = r.json()
    # 有牌价模型:2M 输入 + 0.5M 输出 = ¥2 + ¥1 = ¥3
    flash = next(x for x in d["by_model"] if x["model"] == "deepseek-v4-flash")
    assert flash["estimated_cost"] == 3.0
    assert flash["price_note"]
    # 未知模型不编价
    mystery = next(x for x in d["by_model"] if x["model"] == "mystery-relay-model")
    assert mystery["estimated_cost"] is None
    assert sorted(d["unpriced_models"]) == ["mystery-relay-model"]
    # 汇总只含有牌价部分;口径注明确实存在
    assert d["total_estimated_cost"] == 3.0
    assert d["cost_note"]
    # 峰时提示对象
    ph = d["peak_hours"]
    assert isinstance(ph["is_peak"], bool)
    assert "9:00-12:00" in ph["windows"] and ph["note"]


def test_balance_requires_ownership(usage_env):
    """别人的配置按 404 处理,不泄露存在性。"""
    client, token, uid = usage_env
    from app.db.session import SessionLocal
    from app.db.models import ProviderConfig

    with SessionLocal() as s:
        from app.db.models import User
        other = User(username="fk_other_user")
        s.add(other)
        s.flush()
        other_uid = other.id  # 真实存在的他人(外键开启,假 id 插不进)
        row = ProviderConfig(
            user_id=other_uid, name="x", interface_format="deepseek",
            api_key="enc:v1:whatever", base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
        )
        s.add(row)
        s.commit()
        rid = row.id
    r = client.get(f"/api/settings/providers/{rid}/balance", headers=_auth(token))
    assert r.status_code == 404


def test_balance_unsupported_format(usage_env):
    """openai 兼容没有统一余额接口,501 说清楚,不假装能查。"""
    client, token, uid = usage_env
    from app.db.session import SessionLocal
    from app.db.models import ProviderConfig

    with SessionLocal() as s:
        row = ProviderConfig(
            user_id=uid, name="oa", interface_format="openai",
            api_key="enc:v1:whatever", base_url="https://api.example.com/v1",
            model="gpt-x",
        )
        s.add(row)
        s.commit()
        rid = row.id
    r = client.get(f"/api/settings/providers/{rid}/balance", headers=_auth(token))
    assert r.status_code == 501
    assert "余额接口" in r.json()["detail"]
