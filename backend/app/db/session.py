# app/db/session.py
"""数据库引擎与会话管理。

起步用 SQLite(零配置),日后切 Postgres 只需改 DATABASE_URL。
FastAPI 依赖注入用 get_db;脚本/引擎里用 session_scope 上下文管理器。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")

# SQLite 需要 check_same_thread=False 才能在多线程(FastAPI)下共享连接;
# timeout=30 让并发写(压测+服务)等锁而非立刻报 database is locked。
_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if _is_sqlite
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,
    future=True,
)

if _is_sqlite:
    # WAL:读写不互斥,写锁冲突可被 busy_timeout 化解——否则"读事务升级写"
    # 与用量记录等并发写形成死锁,SQLite 不等 timeout 直接报 database is locked。
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

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
