# tests/test_heal_missing_columns.py
# -*- coding: utf-8 -*-
"""heal_missing_columns:alembic 楔死时的缺列自愈(幂等,只加不删)。

背景(2026-09-30 实测):本地 19 章真实书库的版本戳停在旧迁移上,create_all
曾把表建到比版本戳新,之后的 create_table 迁移每次启动都报 already exists,
新列永远建不出来,projects 查询 500。本测试钉住这道兜底的三件事:
补列带默认值(旧行回填/新行可写)、幂等、翻不出默认值的 NOT NULL 列宁跳过不写坏。
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Table, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.migration import heal_missing_columns


def _stale_engine():
    """模拟「表在、列旧」的楔死库:projects 只有老三列,其余表一概没有。"""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, "
            "title VARCHAR(200) NOT NULL, user_id INTEGER)"
        ))
        conn.execute(text("INSERT INTO projects (id, title, user_id) VALUES (1, '老书', 7)"))
    return eng


def test_heal_adds_columns_with_defaults_and_backfills():
    from app.db.models import Project

    eng = _stale_engine()
    added = heal_missing_columns(target_engine=eng)
    assert "projects.brief" in added
    assert "projects.mode" in added
    assert "projects.mounted_packs" in added

    # 旧行按默认值回填,ORM 能读了
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        p = db.get(Project, 1)
        assert p.mode == "serial" and p.brief == "" and p.mounted_packs == []
    finally:
        db.close()

    # NOT NULL 默认值写进了 DDL:不写这些列也能插新行
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO projects (id, title, user_id) VALUES (2, '新书', 7)"))
        row = conn.execute(text(
            "select mode, brief, audience, mounted_packs from projects where id = 2"
        )).fetchone()
    assert tuple(row) == ("serial", "", "", "[]")


def test_heal_is_idempotent():
    eng = _stale_engine()
    assert heal_missing_columns(target_engine=eng)
    assert heal_missing_columns(target_engine=eng) == []


def test_heal_skips_not_null_without_translatable_default():
    """翻不出 SQL 字面量的 NOT NULL 列(可调用默认/无默认)必须跳过并告警。"""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE heal_probe (id INTEGER PRIMARY KEY)"))
    probe = Table(
        "heal_probe", Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("bad", String(20), nullable=False),  # NOT NULL 且无任何默认
    )
    try:
        assert heal_missing_columns(target_engine=eng) == []
        names = {c["name"] for c in inspect(eng).get_columns("heal_probe")}
        assert "bad" not in names
    finally:
        Base.metadata.remove(probe)
