# tests/test_migrate_llm_usage_duration.py
# -*- coding: utf-8 -*-
"""llm_usage.duration_ms 迁移测试:补列 + 默认值 + 幂等(重启不报错)。"""
from __future__ import annotations

import tempfile

from sqlalchemy import create_engine, inspect, text


def test_add_llm_usage_duration_idempotent(monkeypatch):
    from app import migrate

    tmp = tempfile.mkdtemp(prefix="jw-mig-usage-")
    eng = create_engine(f"sqlite:///{tmp}/mig.db")
    # 建一个缺 duration_ms 的旧 llm_usage 表,模拟存量库
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, model TEXT, "
            "prompt_tokens INTEGER, completion_tokens INTEGER)"
        ))
        conn.execute(text(
            "INSERT INTO llm_usage (model, prompt_tokens, completion_tokens) "
            "VALUES ('m1', 10, 20)"
        ))

    monkeypatch.setattr(migrate, "engine", eng)

    # 第一次:补列,存量行落默认值 0
    migrate._add_llm_usage_duration_column()
    cols = {c["name"] for c in inspect(eng).get_columns("llm_usage")}
    assert "duration_ms" in cols
    with eng.connect() as conn:
        row = conn.execute(text("SELECT duration_ms FROM llm_usage")).first()
    assert row[0] == 0

    # 第二次:幂等,不抛异常
    migrate._add_llm_usage_duration_column()
    cols2 = {c["name"] for c in inspect(eng).get_columns("llm_usage")}
    assert cols2 == cols


def test_add_llm_usage_duration_skips_when_table_absent(monkeypatch):
    """全新库(create_all 会按新模型建表)直接跳过,不报错。"""
    from app import migrate

    tmp = tempfile.mkdtemp(prefix="jw-mig-usage-empty-")
    eng = create_engine(f"sqlite:///{tmp}/mig.db")
    monkeypatch.setattr(migrate, "engine", eng)
    migrate._add_llm_usage_duration_column()  # 不应抛异常
    assert inspect(eng).get_table_names() == []
