# app/migrate.py
# -*- coding: utf-8 -*-
"""启动迁移(阶段 8:多用户;阶段 9:后台管理)。

用的是 create_all 而非 Alembic,已存在的 SQLite 库不会自动补新列。
这里做幂等的轻量迁移:
1. 给旧表补 user_id 列(SQLite 支持 ADD COLUMN);
2. 给 users 表补 is_active 列(存量用户全部置为可用);
3. 建初始 admin 账号(用户名/密码来自配置);
4. 把无主(user_id 为空)的存量数据归到 admin 名下。

每次启动都跑,全部幂等——补过的列/建过的账号会跳过。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import Settings, get_settings
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


def _add_is_active_column() -> None:
    """阶段 9:给 users 表补 is_active 列(存量用户默认可用,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "users" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("users", "is_active"):
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN is_active BOOLEAN "
                    "NOT NULL DEFAULT 1"
                )
            )
            logger.info("迁移:users 补 is_active 列")


def _add_synopsis_column() -> None:
    """给 projects 表补 synopsis 列(书籍简介,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "synopsis"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN synopsis TEXT")
            )
            logger.info("迁移:projects 补 synopsis 列")


def _add_retired_column() -> None:
    """给 entities 表补 retired 列(人物退场标记,存量一律活跃,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "entities" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("entities", "retired"):
            conn.execute(
                text(
                    "ALTER TABLE entities ADD COLUMN retired BOOLEAN "
                    "NOT NULL DEFAULT 0"
                )
            )
            logger.info("迁移:entities 补 retired 列")


def _add_concept_column() -> None:
    """给 projects 表补 concept 列(结构化故事概念 JSON,幂等)。

    SQLite 的 JSON 底层是 TEXT;存量项目该列为 NULL,由灵感工坊逐步填充,
    架构生成在 concept 为空时回落到 topic 一句话(向后兼容)。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "concept"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN concept JSON")
            )
            logger.info("迁移:projects 补 concept 列")


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
        # 还在用代码里的默认密码:仅适合本地开发,务必提醒改掉
        if settings.admin_password == Settings.model_fields["admin_password"].default:
            logger.warning(
                "初始管理员 %s 使用的是默认密码,仅限本地开发;"
                "部署请通过环境变量 ADMIN_PASSWORD 设置强密码,或登录后立即修改",
                settings.admin_username,
            )
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
    _add_is_active_column()
    _add_synopsis_column()
    _add_concept_column()
    _add_retired_column()
    with session_scope() as db:
        admin = _ensure_admin(db)
        db.flush()
        _claim_orphans(db, admin.id)
