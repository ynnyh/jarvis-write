# tests/test_pre_migration_backup.py
# -*- coding: utf-8 -*-
"""迁移前自动备份:快照落盘、保留份数裁剪、pending 检测(启动迁移走真路径覆盖)。"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.migration import (
    _has_pending_migrations,
    _sqlite_file_from_url,
    pre_migration_backup,
)
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _make_db(path, table: str = "projects") -> None:
    con = sqlite3.connect(str(path))
    con.execute(f"create table {table} (id integer primary key)")
    con.commit()
    con.close()


def test_backup_creates_snapshot_and_prunes(tmp_path):
    db_file = tmp_path / "novel.db"
    _make_db(db_file)

    first = pre_migration_backup(db_file, retention=2)
    assert first is not None and first.exists()
    # 再备两份:共 3 份,只保留最新 2 份
    pre_migration_backup(db_file, retention=2)
    pre_migration_backup(db_file, retention=2)

    backups = sorted((tmp_path / "migrations-backup").glob("pre-migration-*.db"))
    assert len(backups) == 2
    # 备份是可读的有效 SQLite,且带源库的表
    con = sqlite3.connect(str(backups[-1]))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    assert "projects" in tables


def test_backup_retention_minimum_clamp(tmp_path):
    db_file = tmp_path / "novel.db"
    _make_db(db_file)
    pre_migration_backup(db_file, retention=0)  # 非法值被钳到 1
    pre_migration_backup(db_file, retention=0)
    backups = list((tmp_path / "migrations-backup").glob("pre-migration-*.db"))
    assert len(backups) == 1


def test_backup_missing_file_returns_none(tmp_path):
    assert pre_migration_backup(tmp_path / "nope.db", retention=5) is None


def test_sqlite_file_from_url():
    assert _sqlite_file_from_url("sqlite:///./jarvis_write.db") is not None
    assert _sqlite_file_from_url("sqlite:///:memory:") is None
    assert _sqlite_file_from_url("postgresql://x") is None


def test_no_pending_migrations_after_startup(client):
    """lifespan 跑完迁移后:当前版本 == 脚本 head,不应再有 pending。"""
    assert _has_pending_migrations() is False
