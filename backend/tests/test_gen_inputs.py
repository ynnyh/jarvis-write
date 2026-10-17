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
