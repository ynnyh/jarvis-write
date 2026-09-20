# tests/test_projects_autoincrement.py
# -*- coding: utf-8 -*-
"""projects.id AUTOINCREMENT 重建迁移(开书串档修复)回归钉。

钉住的 bug:普通 INTEGER PRIMARY KEY 按 max(rowid)+1 分配,删掉 id 最大的
书再新建,新草稿复用同一个 id;前端按 pid 存的向导缓存(提示文字/候选卡/
引擎卡)被灌进新书——「删书重开,提示文字和卡片还是上一次的」。

覆盖:
- 老式表重建后带 AUTOINCREMENT,数据一行不少;
- 重建后删除最大 id 再插入不再复用(核心断言);
- 幂等:跑两遍第二遍是 no-op;
- 残骸自愈:上次中断留下的 projects_new 不挡路。

注:迁移时已不存在的历史 id(高于存量 max)仍可能被重发一次——序列从存量
行起步,这是 SQLite AUTOINCREMENT 的语义;前端以 wiz 缓存键升版 +
created_at 所有权校验兜住这一窗口(见 frontend/src/pages/onboarding/storage.ts)。
"""
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.migrate import rebuild_projects_autoincrement

# 与真实老库同构的最小 DDL:列内联 id + PRIMARY KEY (id) 表约束
_OLD_DDL = """CREATE TABLE projects (
\tid INTEGER NOT NULL,
\ttitle VARCHAR(200) NOT NULL,
\ttopic TEXT NOT NULL,
\tglobal_tendency JSON NOT NULL,
\tcreated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
\tPRIMARY KEY (id)
)"""


@pytest.fixture
def old_db(tmp_path):
    db = str(tmp_path / "wiz.db")
    conn = sqlite3.connect(db)
    conn.execute(_OLD_DDL)
    conn.executemany(
        "INSERT INTO projects (id, title, topic, global_tendency) VALUES (?, ?, ?, ?)",
        [(1, "书一", "t1", "{}"), (2, "书二", "t2", "{}")],
    )
    conn.commit()
    conn.close()
    return db


def _autocommit_conn(db: str):
    # PRAGMA foreign_keys 在事务内是 no-op,重建必须在 AUTOCOMMIT 连接上做
    return create_engine(f"sqlite:///{db}").connect().execution_options(
        isolation_level="AUTOCOMMIT"
    )


def _ddl(db: str) -> str:
    raw = sqlite3.connect(db)
    try:
        return raw.execute(
            "SELECT sql FROM sqlite_master WHERE name='projects'"
        ).fetchone()[0]
    finally:
        raw.close()


def test_rebuild_pins_ids_against_reuse(old_db):
    with _autocommit_conn(old_db) as conn:
        rebuild_projects_autoincrement(conn)
    assert "AUTOINCREMENT" in _ddl(old_db)
    raw = sqlite3.connect(old_db)
    try:
        # 数据一行不少
        ids = [r[0] for r in raw.execute("SELECT id FROM projects ORDER BY id")]
        assert ids == [1, 2]
        # 核心断言:删除刚插入的最大 id 后,新插入必须跳过它(老行为会复用同一 id)
        cur = raw.execute(
            "INSERT INTO projects (title, topic, global_tendency) VALUES ('新书', 't', '{}')")
        first = cur.lastrowid
        raw.execute("DELETE FROM projects WHERE id = ?", (first,))
        cur = raw.execute(
            "INSERT INTO projects (title, topic, global_tendency) VALUES ('又一本', 't', '{}')")
        assert cur.lastrowid == first + 1, f"删除 id={first} 后复用了它,串档 bug 回归"
    finally:
        raw.close()


def test_rebuild_is_idempotent(old_db):
    with _autocommit_conn(old_db) as conn:
        rebuild_projects_autoincrement(conn)
        ddl_first = _ddl(old_db)
        rebuild_projects_autoincrement(conn)  # 第二遍必须安静跳过
        assert _ddl(old_db) == ddl_first


def test_rebuild_survives_leftover_projects_new(old_db):
    raw = sqlite3.connect(old_db)
    raw.execute("CREATE TABLE projects_new (id INTEGER PRIMARY KEY)")
    raw.commit()
    raw.close()
    with _autocommit_conn(old_db) as conn:
        rebuild_projects_autoincrement(conn)
    assert "AUTOINCREMENT" in _ddl(old_db)


def test_rebuild_noop_on_missing_table(tmp_path):
    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    with _autocommit_conn(db) as conn:
        rebuild_projects_autoincrement(conn)  # 无 projects 表:安静返回不抛
    raw = sqlite3.connect(db)
    try:
        tables = raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert not tables
    finally:
        raw.close()
