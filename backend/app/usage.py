# app/usage.py
# -*- coding: utf-8 -*-
"""功能使用计数:动作级最小埋点,给「哪个工坊/哪条线值得继续投入」提供数据回路。

设计取舍:
- 只记**动作**(非 GET 的已鉴权请求):GET 里有 3 秒轮询(useJob),记进去全是噪声;
- 只记 次数 + 最后时间,不记内容与路径参数——数据在用户自己的库里
  (桌面版不出本机,网页版随库备份),没有上行遥测;
- 内存缓冲 + 30s 批量 upsert:埋点在请求路径上,必须零感知——SQLite 单写者,
  一请求一写会和生成任务的写库互相卡。

判定口径见 feature_of():六条制片线 + novel 主线;auth/admin/settings/system
等基础设施数不计。llm_usage 记的是 token 成本账(按模型),这边记的是
「动没动过」的使用账(按功能线)——两本账互补,不互相替代。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

logger = logging.getLogger("jarvis-write.usage")

_FLUSH_INTERVAL_S = 30.0

# {(feature, user_id): 次数}——请求路径上的写入只进内存,落库由周期任务批量做
_buf: dict[tuple[str, int], int] = {}
_lock = threading.Lock()
_task: asyncio.Task | None = None

# /api 下一级段 → 功能线。projects 下含 /drama 的归 drama,其余归 novel(主线)。
_SEGMENT_MAP: dict[str, str] = {
    "series": "series",
    "clips": "clips",
    "inspire": "inspire",
    "birthday": "birthday",
    "promos": "promo",
    "projects": "novel",
    "tendency": "novel",
}


def feature_of(path: str) -> str | None:
    """请求路径 → 功能线;返回 None 表示不计(auth/admin/settings/system 等)。"""
    if not path.startswith("/api/"):
        return None
    if "/drama" in path:
        return "drama"
    seg = path[len("/api/"):].split("/", 1)[0]
    return _SEGMENT_MAP.get(seg)


def record(method: str, path: str, user_id: int) -> None:
    """记一次动作(内存缓冲,不碰库)。GET/HEAD/OPTIONS 是读,不计。"""
    if method in ("GET", "HEAD", "OPTIONS"):
        return
    feature = feature_of(path)
    if feature is None:
        return
    with _lock:
        key = (feature, user_id)
        _buf[key] = _buf.get(key, 0) + 1


def flush() -> None:
    """把缓冲 upsert 进 feature_usage。落库失败把增量并回缓冲,下轮自愈。"""
    with _lock:
        pending = dict(_buf)
        _buf.clear()
    if not pending:
        return
    from app.db.models import FeatureUsage
    from app.db.session import SessionLocal

    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            # SQLite 用方言 insert 拿 ON CONFLICT;insert() 兜底其他方言(同语义)
            stmt_factory = sqlite_insert if db.bind.dialect.name == "sqlite" else insert
            for (feature, user_id), n in pending.items():
                db.execute(
                    stmt_factory(FeatureUsage)
                    .values(feature=feature, user_id=user_id, uses=n, last_used_at=now)
                    .on_conflict_do_update(
                        index_elements=[FeatureUsage.feature, FeatureUsage.user_id],
                        set_={
                            "uses": FeatureUsage.uses + n,
                            "last_used_at": now,
                        },
                    )
                )
            db.commit()
        logger.info("使用计数落库:%d 个功能×用户组合", len(pending))
    except Exception:  # noqa: BLE001 — 埋点绝不影响主流程,丢了下轮周期再攒
        logger.warning("使用计数落库失败,增量并回缓冲", exc_info=True)
        with _lock:
            for key, n in pending.items():
                _buf[key] = _buf.get(key, 0) + n


async def _loop() -> None:
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL_S)
        flush()


def start_flush_loop() -> None:
    """应用启动时挂上周期落库任务(lifespan 调)。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop_flush_loop() -> None:
    """应用停机时取消任务并把残余缓冲落库(lifespan 调)。"""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
    flush()
