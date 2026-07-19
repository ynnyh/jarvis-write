# app/db/session.py
"""数据库引擎与会话管理。

起步用 SQLite(零配置),日后切 Postgres 只需改 DATABASE_URL。
FastAPI 依赖注入用 get_db;脚本/引擎里用 session_scope 上下文管理器。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# SQLite 需要 check_same_thread=False 才能在多线程(FastAPI)下共享连接。
_connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖:每个请求一个会话,结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/引擎内部使用:自动提交,异常回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
