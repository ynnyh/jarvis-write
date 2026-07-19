# app/migrate.py
# -*- coding: utf-8 -*-
"""启动迁移(阶段 8:多用户)。

用的是 create_all 而非 Alembic,已存在的 SQLite 库不会自动补新列。
这里做幂等的轻量迁移:
1. 给旧表补 user_id 列(SQLite 支持 ADD COLUMN);
2. 建初始 admin 账号(用户名/密码来自配置);
3. 把无主(user_id 为空)的存量数据归到 admin 名下。

每次启动都跑,全部幂等——补过的列/建过的账号会跳过。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.db.models import User
from app.db.session import engine, session_scope

logger = logging.getLogger("jarvis-write.migrate")

# 需要补 user_id 的旧表
_TABLES_NEEDING_USER = ("projects", "provider_settings", "llm_usage")


def _column_exists(table: str, column: str) -> bool:
    insp = inspect(engine)
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:  # noqa: BLE001 — 表不存在等
        return False
    return column in cols


def _add_user_id_columns() -> None:
    """给旧表补 user_id 列(仅 SQLite / 幂等)。"""
    with engine.begin() as conn:
        for table in _TABLES_NEEDING_USER:
            insp = inspect(conn)
            if table not in insp.get_table_names():
                continue  # create_all 会新建,无需补列
            if not _column_exists(table, "user_id"):
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
                )
                logger.info("迁移:%s 补 user_id 列", table)


def _ensure_admin(db: Session) -> User:
    settings = get_settings()
    admin = (
        db.query(User).filter(User.username == settings.admin_username).first()
    )
    if admin is None:
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        logger.info("迁移:创建初始管理员 %s", settings.admin_username)
    return admin


def _claim_orphans(db: Session, admin_id: int) -> None:
    """把 user_id 为空的存量数据归到 admin。"""
    for table in _TABLES_NEEDING_USER:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            continue
        result = db.execute(
            text(
                f"UPDATE {table} SET user_id = :uid "
                "WHERE user_id IS NULL"
            ),
            {"uid": admin_id},
        )
        if result.rowcount:
            logger.info("迁移:%s 归属 admin 共 %d 行", table, result.rowcount)


def run_migrations() -> None:
    """启动时调用。幂等。"""
    _add_user_id_columns()
    with session_scope() as db:
        admin = _ensure_admin(db)
        db.flush()
        _claim_orphans(db, admin.id)
