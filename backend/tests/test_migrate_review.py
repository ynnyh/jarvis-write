# tests/test_migrate_review.py
# -*- coding: utf-8 -*-
"""审校把关配置列迁移测试:补列 + 默认值 + 幂等(重启不报错、不覆盖)。"""
from __future__ import annotations

import tempfile

from sqlalchemy import create_engine, inspect, text


def test_add_review_columns_idempotent(monkeypatch):
    from app import migrate

    tmp = tempfile.mkdtemp(prefix="jw-mig-review-")
    eng = create_engine(f"sqlite:///{tmp}/mig.db")
    # 建一个缺审校配置列的旧 projects 表,模拟存量库
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO projects (title) VALUES ('老项目')"))

    monkeypatch.setattr(migrate, "engine", eng)

    # 第一次:补三列
    migrate._add_review_columns()
    cols = {c["name"] for c in inspect(eng).get_columns("projects")}
    assert {"review_pass_threshold", "review_auto_revise", "review_max_revisions"} <= cols

    # 默认值:阈值 7 / 自动回炉开 / 上限 3
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT review_pass_threshold, review_auto_revise, review_max_revisions "
            "FROM projects"
        )).first()
    assert row[0] == 7
    assert row[1] in (1, True)
    assert row[2] == 3

    # 第二次:幂等,列已在 → 跳过,不抛异常
    migrate._add_review_columns()
    cols2 = {c["name"] for c in inspect(eng).get_columns("projects")}
    assert cols2 == cols
