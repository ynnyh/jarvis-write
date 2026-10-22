# tests/test_llm_failover.py
# -*- coding: utf-8 -*-
"""跨渠道 failover(渠道轮换)测试。

背景(2026-10-22 服务器数据复盘):用户「白子」配了 3 个渠道,蓝图环节默认
渠道连吃 429 时系统只会死磕默认那个,横跨 4 天重试 10 次全撞同一堵墙,用户
就此流失。本文件锁四件事:
- 候选构建:排除主渠道/同源同模型,主渠道来自 .env 时不开轮换;
- 轮换触发面:只接「换渠道有用」的错(瞬时耗尽/401/402/403),安全过滤、
  参数错这类换渠道也治不了的直抛;
- 轮换成功与全灭:成功即返回候选正文;全灭合并每个渠道的死因;
- 错误归一:NO_PROVIDER 打前端标记,轮换合并清单不被单状态码分支改写。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.llm.base import (
    EmptyContentError,
    LLMMessage,
    LLMResponse,
    UpstreamError,
    _failover_eligible,
    ask_with_failover,
)
from app.llm.factory import (
    ProviderNotConfigured,
    build_failover_adapters,
    resolve_tier_config,
)
from app.jobs import normalize_job_error
from app.main import app

INVITE = "test-invite"


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


def _create(client: TestClient, headers: dict, **kw) -> dict:
    body = {"interface_format": "openai-compatible", "api_key": "sk-x", **kw}
    r = client.post("/api/settings/providers", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


class _FakeAdapter:
    """鸭子类型假适配器:complete 按 script 弹出演出(异常则抛,否则当正文)。

    真实链路里「渠道内 3 次退避重试」在 complete() 内部;这里直接抛 UpstreamError
    等价于「该渠道重试已耗尽」,正是 failover 接手的现场。
    """

    def __init__(self, script, config_name: str = "", model_name: str = "m"):
        self.script = list(script)
        self.config_name = config_name
        self.model_name = model_name
        self.max_tokens = 8192
        self.failover_adapters: list = []
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model=self.model_name, finish_reason="stop")


def _msgs() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="hi")]


def _rate_limited() -> UpstreamError:
    return UpstreamError("上游返回 HTTP 429: rate limited", status=429, retryable=True)


# ---------- 轮换触发面 ----------


def test_failover_eligible_matrix():
    """429/5xx 耗尽与 401/402/403 可轮换;安全过滤/参数错/无关异常不轮换。"""
    assert _failover_eligible(_rate_limited())
    assert _failover_eligible(
        UpstreamError("server busy", status=502, retryable=True)
    )
    for status in (401, 402, 403):
        assert _failover_eligible(
            UpstreamError(f"HTTP {status}", status=status, retryable=False)
        )
    # 换渠道也治不了的:安全过滤的空正文、参数错、模型不存在、与上游无关的异常
    assert not _failover_eligible(
        EmptyContentError("被安全策略拦下", budget_bound=False)
    )
    assert not _failover_eligible(
        UpstreamError("参数不支持", status=400, retryable=False)
    )
    assert not _failover_eligible(
        UpstreamError("模型不存在", status=404, retryable=False)
    )
    assert not _failover_eligible(RuntimeError("与上游无关"))


# ---------- 轮换成功 / 全灭 / 不该轮换时不轮换 ----------


def test_ask_with_failover_switches_to_backup():
    """主渠道重试耗尽 → 自动切候选渠道,拿到正文;候选不被递归挂候选。"""
    primary = _FakeAdapter([_rate_limited()], config_name="官方ds")
    backup = _FakeAdapter(["好的"], config_name="魔芋")
    primary.failover_adapters = [backup]

    out = asyncio.run(ask_with_failover(primary, _msgs()))

    assert out == "好的"
    assert primary.calls == 1
    assert backup.calls == 1


def test_ask_with_failover_all_dead_combined_message():
    """全部渠道失败:合并清单带「已自动轮换」与每个渠道的可读名+死因。"""
    primary = _FakeAdapter([_rate_limited()], config_name="官方ds")
    backup = _FakeAdapter(
        [UpstreamError("上游返回 HTTP 402: 余额不足", status=402, retryable=False)],
        config_name="魔芋",
    )
    primary.failover_adapters = [backup]

    with pytest.raises(UpstreamError) as ei:
        asyncio.run(ask_with_failover(primary, _msgs()))

    msg = str(ei.value)
    assert "已自动轮换" in msg
    assert "官方ds" in msg and "魔芋" in msg
    assert "402" in msg
    assert ei.value.__cause__ is not None


def test_ask_with_failover_skips_ineligible():
    """安全过滤的空正文:换渠道也没用,直抛不烧候选渠道。"""
    primary = _FakeAdapter(
        [EmptyContentError("上游拒绝输出", budget_bound=False)], config_name="官方ds"
    )
    backup = _FakeAdapter(["好的"], config_name="魔芋")
    primary.failover_adapters = [backup]

    with pytest.raises(EmptyContentError):
        asyncio.run(ask_with_failover(primary, _msgs()))
    assert backup.calls == 0


def test_ask_with_failover_without_candidates_raises_original():
    """没有候选渠道(单配置用户):行为与旧版完全一致,抛原错误。"""
    primary = _FakeAdapter([_rate_limited()], config_name="官方ds")
    with pytest.raises(UpstreamError) as ei:
        asyncio.run(ask_with_failover(primary, _msgs()))
    assert "429" in str(ei.value)


# ---------- 候选构建(DB 配置驱动) ----------


def test_build_failover_candidates_excludes_primary_and_duplicates(client):
    """候选排除主渠道与同源同模型;task_label/config_name 打标不串档。"""
    tok = _register(client, "fo_candidates")["token"]
    headers = _auth(tok)
    _create(client, headers, name="主力", base_url="https://a.com/v1", model="m-a")
    _create(
        client, headers, name="备用", interface_format="anthropic",
        base_url="https://b.com/v1", model="m-b",
    )
    _create(client, headers, name="重复", base_url="https://a.com/v1", model="m-a")

    from app.auth import current_user_id

    uid = current_user_id.set(client.get("/api/auth/me", headers=headers).json()["id"])
    try:
        primary_cfg = resolve_tier_config("quality")
        assert primary_cfg["name"] == "主力"

        cands = build_failover_adapters(
            primary_cfg, overrides={"temperature": 0.5}, task_label="title"
        )
        assert [c.config_name for c in cands] == ["备用"]
        assert cands[0].model_name == "m-b"
        assert cands[0].task_label == "title"

        # get_adapter_for 挂候选:主渠道失败时可换「备用」
        from app.llm.router import Task, get_adapter_for

        adapter = get_adapter_for(Task.TITLE)
        assert adapter.task_label == "title"
        assert [c.config_name for c in adapter.failover_adapters] == ["备用"]
    finally:
        current_user_id.reset(uid)


# ---------- 错误归一(任务路径) ----------


def test_normalize_job_error_marks_no_provider_and_passes_failover_list():
    """NO_PROVIDER 打前端标记;轮换合并清单原样放行;单渠道 429 仍归一成人话。"""
    marked = normalize_job_error(
        ProviderNotConfigured("还没有可用的写作渠道:配好一个模型的 API Key 就能开始。")
    )
    assert marked.startswith("[NO_PROVIDER]")
    assert "API Key" in marked

    combined = UpstreamError(
        "3 个渠道都失败了(已自动轮换),最后错误:\n"
        "· 官方ds:上游返回 HTTP 429\n· 魔芋:上游返回 HTTP 402",
        retryable=True,
    )
    assert normalize_job_error(combined) == str(combined)

    single = normalize_job_error(_rate_limited())
    assert "限流" in single and "已自动轮换" not in single
