# tests/test_db_migration.py
# -*- coding: utf-8 -*-
"""Alembic 迁移运行时的回归钉(全测试套件里唯一真正执行迁移的文件)。

钉住接入验收时真实踩过的坑:
1. 全新库 upgrade head 必须建出与模型完全一致的表——基线迁移漏一张表,
   新装用户就缺一张表(create_all 兜底能补,但口径就歪了);
2. 老库(pre-Alembic)自动 stamp 到基线:只写版本号、绝不重复建表/动数据;
3. 迁移跑完业务 logger 必须还活着——env.py 曾用 fileConfig(默认
   disable_existing_loggers=True)把 setup_logging 建好的日志全打哑,
   生产表现为"启动一次之后所有日志静默消失";
4. 从任意 CWD 调用都能找到迁移脚本——桌面版冻结环境的工作目录不在
   解包目录,alembic.ini 的 script_location 必须按 ini 自身位置解析。

每个用例都把 DATABASE_URL 指到独立临时库,并 monkeypatch 掉 migration/session
模块级 engine,不碰 conftest 的共享测试库;结束必须清 get_settings 的
lru_cache,否则缓存的 URL 会漏给后续用例。
"""
from __future__ import annotations

import logging
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.db.migration import BASELINE_REVISION, run_alembic_migrations


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """独立临时库 + 干净的引擎/配置上下文;顺便把 CWD 切走钉住"路径与 CWD 无关"。"""
    db_path = tmp_path / "migration-check.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)  # 故意离开 backend/:CWD 无关性是验收踩过的坑

    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_engine(url, poolclass=NullPool)
    import app.db.migration as mig
    import app.db.session as sess

    monkeypatch.setattr(mig, "engine", engine)
    monkeypatch.setattr(sess, "engine", engine)
    yield db_path, engine
    # 环境变量由 monkeypatch 恢复;缓存必须手动清,否则本用例的 URL 漏给后续用例
    get_settings.cache_clear()
    engine.dispose()


def test_fresh_db_upgrade_creates_all_model_tables(isolated_db):
    """全新库:upgrade head 建出的表与模型元数据完全一致,版本钉在基线。"""
    db_path, engine = isolated_db
    run_alembic_migrations()

    import app.db.models  # noqa: F401  注册全部模型
    from app.db.base import Base

    expected = set(Base.metadata.tables.keys())
    # alembic_version 是 Alembic 自己的版本表,不在业务模型里
    migrated = set(inspect_tables(engine)) - {"alembic_version"}
    assert migrated == expected, (
        f"基线迁移与模型不一致:缺 {sorted(expected - migrated)},多 {sorted(migrated - expected)}"
    )
    version = sqlite3.connect(db_path).execute(
        "SELECT version_num FROM alembic_version"
    ).fetchall()
    assert version == [(BASELINE_REVISION,)]


def inspect_tables(engine) -> list[str]:
    from sqlalchemy import inspect

    return inspect(engine).get_table_names()


def test_legacy_db_stamps_without_touching_data(isolated_db):
    """老库(pre-Alembic):有业务表、无 alembic_version → 只 stamp,数据分毫不动。"""
    db_path, engine = isolated_db
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO projects (title) VALUES ('用户的真实作品')")
    conn.commit()
    conn.close()

    run_alembic_migrations()

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT version_num FROM alembic_version"
    ).fetchall() == [(BASELINE_REVISION,)]
    # stamp 不许重建表、不许丢数据
    assert conn.execute("SELECT title FROM projects").fetchall() == [("用户的真实作品",)]
    conn.close()
    # 之后 create_all(lifespan 的兜底步骤)能安全补齐其余表
    import app.db.models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    assert "mood_clips" in inspect_tables(engine)


def test_migrations_leave_app_loggers_alive(isolated_db, caplog):
    """迁移跑完,业务 logger 必须未被禁用、警告能正常发出并被捕获。

    env.py 曾 fileConfig(disable_existing_loggers=True),生产表现为启动后日志静默。
    """
    run_alembic_migrations()

    logger = logging.getLogger("jarvis-write.bible")
    assert not logger.disabled, "迁移把业务 logger 禁用了(检查 env.py 是否又在 fileConfig)"
    with caplog.at_level(logging.WARNING, logger="jarvis-write.bible"):
        logger.warning("迁移后日志必须仍然可见")
    assert "迁移后日志必须仍然可见" in caplog.text


def test_business_loggers_alive_on_legacy_stamp_path(isolated_db, caplog):
    """老库 stamp 路径同样不许动日志(两条路径各自过一遍 env.py)。"""
    conn = sqlite3.connect(isolated_db[0])
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)")
    conn.commit()
    conn.close()

    run_alembic_migrations()

    with caplog.at_level(logging.WARNING, logger="jarvis-write.ledger"):
        logging.getLogger("jarvis-write.ledger").warning("stamp 后日志仍然可见")
    assert "stamp 后日志仍然可见" in caplog.text
