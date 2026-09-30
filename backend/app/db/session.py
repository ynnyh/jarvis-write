# app/db/session.py
"""数据库引擎与会话管理。

起步用 SQLite(零配置),日后切 Postgres 只需改 DATABASE_URL。
FastAPI 依赖注入用 get_db;脚本/引擎里用 session_scope 上下文管理器。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import os

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

# ---- 连接池:必须是显式的,不能吃 SQLAlchemy 默认值 ----
#
# 为什么这是 P0(2026-09-28 压测实测,scripts/loadcheck.py):
# 不传 pool_size / max_overflow 时 SQLAlchemy 默认是 5 + 10 = **全站 15 条连接**。
# 而每个已鉴权请求光 `get_current_user` 就要 `db.get(User)` 占一条,于是 50 并发
# 一上来就 30/50 个请求在排队等连接,抛:
#   sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
#   connection timed out, timeout 30.00
# 更糟的是这些端点是 `async def` 里跑同步 SQLAlchemy —— **等连接发生在事件循环
# 线程上**,一卡就是全站卡死:压测里连零 DB 的 /api/health 都跟着 20s 超时。
#
# 给多少:SQLite 读并发不受写锁限制(WAL),这里要抗的是「同时等连接」的请求数。
# 20 常驻 + 40 突发 ≈ 能扛住一台机器上几十个并发用户;再高就该换 Postgres,
# 而不是继续调这个数字(单写者的天花板在 SQLite 本身,不在连接池)。
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "40"))
POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,
    future=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    # 回收长连接:SQLite 文件句柄与中间代理的空闲连接都可能过期
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=True,
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
        # 外键约束(P1-6):模型里声明的 ondelete(CASCADE/SET NULL)由此生效,
        # 脏引用在写入时即被拒,不再依赖人工记得逐表清理。删除路径本来就有
        # 手工级联(deps.delete_project_cascade),两者不冲突——约束是最后防线。
        cur.execute("PRAGMA foreign_keys=ON")
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
