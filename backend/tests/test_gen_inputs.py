# tests/test_gen_inputs.py
# -*- coding: utf-8 -*-
"""生成输入留痕(docs/29 / docs/32 §2.4)。

为什么值得测:留痕的价值全在「写进去了」和「绝不拖垮生成」这两点——它自己失败
必须静默,而它该覆盖的环节不能悄悄少一个(少一个就等于那段复盘永久空白)。
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_record_gen_inputs_clips_and_defaults_job_id(client):
    """裁剪:长文本截断、长列表限量、dict 递归一层;job_id 缺省取当前任务上下文。"""
    from app import audit, live
    from app.db.models import GenInput
    from app.db.session import SessionLocal

    token = live.current_job_id.set("job-abc12345")
    try:
        audit.record_gen_inputs(
            kind="unit-clip",
            payload={
                "long": "字" * 9000,
                "many": list(range(200)),
                "nested": {"k": "v", "big": "x" * 9000},
                "num": 3,
            },
            project_id=None,
        )
    finally:
        live.current_job_id.reset(token)

    with SessionLocal() as db:
        row = (
            db.query(GenInput).filter_by(kind="unit-clip")
            .order_by(GenInput.id.desc()).first()
        )
        assert row is not None
        assert row.job_id == "job-abc12345"          # 自动带上当前任务
        assert len(row.payload["long"]) == 4000 + len("…(截断)")
        assert len(row.payload["many"]) == 50
        assert len(row.payload["nested"]["big"]) == 4000 + len("…(截断)")
        assert row.payload["num"] == 3


def test_record_gen_inputs_never_raises(client):
    """留痕失败必须静默:给个不可 JSON 序列化的对象,也不能把生成拖下水。"""
    from app import audit

    audit.record_gen_inputs(kind="unit-boom", payload={"bad": object()})


def test_gen_inputs_uses_caller_session(client):
    """传 db 时用调用方会话(随本次事务提交),不自己开短会话。"""
    from app import audit
    from app.db.models import GenInput
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        audit.record_gen_inputs(kind="unit-session", payload={"a": 1}, db=db)
        db.commit()
        assert db.query(GenInput).filter_by(kind="unit-session").count() == 1


def test_gen_input_sites_present():
    """八处埋点不许悄悄消失(源码级回归守卫:每个环节模块里都得有对应 kind)。"""
    from app.api.chapters import _job
    from app.api.projects import architecture, blueprint, brief, chain, intent, plans, skeleton

    expected = {
        intent: "intent-readback",
        plans: "book-plans",
        architecture: "arch-layer",
        skeleton: "skeleton",
        blueprint: "blueprint",
        _job: "chapter-revision",
        brief: "concept-develop",
        chain: "sample-chain",
    }
    missing = []
    for module, kind in expected.items():
        source = inspect.getsource(module)
        if f'kind="{kind}"' not in source:
            missing.append(f"{module.__name__}:{kind}")
    assert not missing, f"留痕埋点缺失:{missing}"


# —— docs/37 留痕加固:归属用户 / 重试台账 / 环节名 / 方向卡历史 ——
def test_record_gen_inputs_captures_user_id(client):
    """无项目上下文的输入也要按人归档(灵感工坊那批留痕的归属前提)。"""
    from app import audit, live
    from app.auth import current_user_id
    from app.db.models import GenInput
    from app.db.session import SessionLocal

    t_user = current_user_id.set(42)
    t_job = live.current_job_id.set(None)
    try:
        audit.record_gen_inputs(kind="unit-user", payload={"a": 1})
    finally:
        live.current_job_id.reset(t_job)
        current_user_id.reset(t_user)

    with SessionLocal() as db:
        row = (
            db.query(GenInput).filter_by(kind="unit-user")
            .order_by(GenInput.id.desc()).first()
        )
        assert row is not None and row.user_id == 42 and row.project_id is None


def test_retry_ledger_persists(client):
    """渠道重试必须落持久台账(llm_retries):有 job 记 job,纯请求路径也要留。"""
    from app.auth import current_user_id
    from app.db.models import LlmRetry
    from app.db.session import SessionLocal
    from app.llm.base import UpstreamError, _notice_retry
    from app.live import current_job_id

    t_user = current_user_id.set(7)
    t_job = current_job_id.set("job-retry01")
    try:
        _notice_retry(UpstreamError("上游限流", retryable=True), 1, 3, label="polish")
    finally:
        current_job_id.reset(t_job)
        current_user_id.reset(t_user)

    with SessionLocal() as db:
        row = (
            db.query(LlmRetry).filter_by(label="polish")
            .order_by(LlmRetry.id.desc()).first()
        )
        assert row is not None
        assert row.user_id == 7 and row.job_id == "job-retry01"
        assert row.attempt == 1 and row.total == 3
        assert "限流" in row.error

    # 无 job 的请求路径重试:台账照记(job_id 空),这是此前完全丢失的盲区
    t_user = current_user_id.set(7)
    t_job = current_job_id.set(None)
    try:
        _notice_retry(UpstreamError("连接被掐断", retryable=True), 2, 3, label="naming")
    finally:
        current_job_id.reset(t_job)
        current_user_id.reset(t_user)
    with SessionLocal() as db:
        row = (
            db.query(LlmRetry).filter_by(label="naming")
            .order_by(LlmRetry.id.desc()).first()
        )
        assert row is not None and row.job_id is None and row.attempt == 2


def test_llm_usage_records_task_label(client):
    """llm_usage.task:环节名进成本账,分环节归因的源头。"""
    from types import SimpleNamespace

    from app.db.models import LlmUsage
    from app.db.session import SessionLocal
    from app.llm.base import LLMAdapter, LLMResponse

    dummy = SimpleNamespace(task_label="evolution")  # 鸭子类型:只有打标属性也要能记账
    LLMAdapter._record_usage(
        dummy,
        LLMResponse(content="ok", model="test-model", prompt_tokens=10,
                    completion_tokens=5, finish_reason="stop", truncated=False),
        duration_ms=123,
    )
    with SessionLocal() as db:
        row = (
            db.query(LlmUsage).filter_by(task="evolution")
            .order_by(LlmUsage.id.desc()).first()
        )
        assert row is not None
        assert row.model == "test-model" and row.duration_ms == 123


def test_book_plans_payload_keeps_cards(client, monkeypatch):
    """方向卡批次全文入留痕:book_plans 列「换一批」即覆盖,产物不记就永久丢。"""
    import json as _json
    from unittest.mock import patch

    from app.db.models import GenInput
    from app.db.session import SessionLocal

    r = client.post(
        "/api/auth/register",
        json={"username": "gi_cards_user", "password": "pass123", "invite_code": "test-invite"},
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/projects", headers=headers, json={"title": "卡史书"})
    pid = r.json()["id"]

    plans_json = _json.dumps({"plans": [
        {"title": "甲方案", "kernel": "k1", "protagonist": "p1", "world": "w1",
         "arc": "a1", "engine": "e1", "flavor": ["冷"], "scale": "长篇",
         "scale_reason": "r", "label": "l1"},
        {"title": "乙方案", "kernel": "k2", "protagonist": "p2", "world": "w2",
         "arc": "a2", "engine": "e2", "flavor": ["燃"], "scale": "连载",
         "scale_reason": "r2", "label": "l2"},
    ]}, ensure_ascii=False)

    class _Fake:
        def __init__(self, payload):
            self.payload = payload
            self.last_prompt = ""

        async def ask(self, prompt, system=None):
            self.last_prompt = prompt
            return self.payload

    with patch("app.api.projects.plans.get_adapter_for", return_value=_Fake(plans_json)):
        r = client.post(f"/api/projects/{pid}/book-plans", headers=headers,
                        json={"mode": "serial", "topic": "一个点子", "genre": ""})
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        row = (
            db.query(GenInput).filter_by(kind="book-plans", project_id=pid)
            .order_by(GenInput.id.desc()).first()
        )
        assert row is not None
        cards = row.payload.get("cards")
        assert isinstance(cards, list) and len(cards) == 2
        assert cards[0]["title"] == "甲方案" and cards[1]["title"] == "乙方案"
        # 归属用户(docs/37)
        assert row.user_id is not None


def test_book_plans_accepts_long_topic(client):
    """想法(topic)超 500 字不再被 422 拦——与 intent 线 2000 对齐;超 2000 仍拒。

    2026-10-20 用户实测「挑一整套书方向」页报错只显示 [object Object]:根因是
    建项目/PATCH 不限长的 topic 撞上 book-plans 的 500 历史闸门。
    """
    import json as _json
    from unittest.mock import patch

    r = client.post(
        "/api/auth/register",
        json={"username": "plans_long_topic", "password": "pass123", "invite_code": "test-invite"},
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/projects", headers=headers, json={"title": "长想法书"})
    pid = r.json()["id"]

    plans_json = _json.dumps({"plans": [
        {"title": "甲方案", "kernel": "k1", "protagonist": "p1", "world": "w1",
         "arc": "a1", "engine": "e1", "flavor": ["冷"], "scale": "长篇",
         "scale_reason": "r", "label": "l1"},
        {"title": "乙方案", "kernel": "k2", "protagonist": "p2", "world": "w2",
         "arc": "a2", "engine": "e2", "flavor": ["燃"], "scale": "连载",
         "scale_reason": "r2", "label": "l2"},
    ]}, ensure_ascii=False)

    class _Fake:
        async def ask(self, prompt, system=None):
            return plans_json

    # 800 字:旧 500 闸门必拦(422),新口径放行
    long_topic = "修" * 800
    with patch("app.api.projects.plans.get_adapter_for", return_value=_Fake()):
        r = client.post(f"/api/projects/{pid}/book-plans", headers=headers,
                        json={"mode": "serial", "topic": long_topic})
    assert r.status_code == 200, r.text

    # 上限仍在:2001 字被校验拦下(422;前端 v0.59.3 起会显示可读原因)
    r = client.post(f"/api/projects/{pid}/book-plans", headers=headers,
                    json={"mode": "serial", "topic": "修" * 2001})
    assert r.status_code == 422
    # 建项目同口径:超长想法在源头就被拦,不再绕道落库
    assert client.post("/api/projects", headers=headers,
                       json={"title": "又一本", "topic": "修" * 2001}).status_code == 422
