# tests/test_extract_concurrency.py
# -*- coding: utf-8 -*-
"""S1/M2 并发回归:extract_and_apply 不得「拿着写锁跨 LLM 调用」。

这是 455e6f0 反复打补丁的那类 database is locked 的病根:抽取先 purge(写)拿到
WAL 写锁,却跨过整段 FACT_EXTRACT 的 LLM 调用才提交 —— 期间任何别的连接(如用量
记账)要写都被堵到 LLM 时长,叠加读快照过期时更会直接报 database is locked。

修复后的纪律:LLM 调用前必须先 commit(释放写锁 + 读快照)。本测试在 mock LLM 的
ask() 里用另一条独立连接(短 busy_timeout)写一次库:
  修复前 → extract 攥着 purge 写锁横跨 ask,内层写阻塞到超时 → database is locked;
  修复后 → ask 之前已 commit,无锁 → 内层写秒过。

用真·文件库(conftest 指向临时 test.db,连接事件里开 WAL + busy_timeout),两条独立
连接才测得出锁竞争 —— 既有 async-jobs 测试 mock LLM 秒回、单连接,复现不了这类 bug。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


async def _no_lock_across_llm_case() -> None:
    from sqlalchemy import text

    from app.db.base import Base
    import app.db.models  # noqa: F401 — 注册全部表(Fact/Entity/Foreshadowing…)
    from app.db.models import Entity, Fact, Project
    from app.db.session import SessionLocal, engine
    from app.engines.consistency import extractor as extractor_mod

    Base.metadata.create_all(engine)

    # 建项目 + 一条第 5 章的事实:purge 才会真的 DELETE 一行、真的拿到写锁。
    setup = SessionLocal()
    proj = Project(title="orig")
    setup.add(proj)
    setup.flush()
    pid = proj.id
    ent = Entity(project_id=pid, entity_type="character", name="林晚")
    setup.add(ent)
    setup.flush()
    setup.add(Fact(
        project_id=pid, entity_id=ent.id, fact_type="state",
        content="受伤", valid_from=5, valid_until=None, source_chapter=5,
    ))
    setup.commit()
    setup.close()

    inner: dict = {"ok": None, "err": None}

    class _ProbeAdapter:
        """假 LLM:ask() 期间用另一条连接写一次,探测 extract 是否还攥着写锁。"""

        async def ask(self, prompt: str, system: str | None = None) -> str:
            try:
                with engine.connect() as c:
                    c.execute(text("PRAGMA busy_timeout=800"))  # 别真等 30s
                    c.execute(
                        text("UPDATE projects SET title='inner' WHERE id=:i"),
                        {"i": pid},
                    )
                    c.commit()
                inner["ok"] = True
            except Exception as exc:  # noqa: BLE001
                inner["ok"] = False
                inner["err"] = f"{type(exc).__name__}: {exc}"[:120]
            return "{}"

    sa = SessionLocal()
    with patch.object(
        extractor_mod, "get_adapter_for", return_value=_ProbeAdapter()
    ):
        await extractor_mod.extract_and_apply(sa, pid, 5, "第五章正文……")
    sa.close()

    assert inner["ok"] is True, (
        "LLM 调用期间另一连接写入被阻塞,说明 extract_and_apply 仍拿着写锁跨 LLM 调用"
        f"(违反 commit 纪律):{inner['err']}"
    )


def test_extract_and_apply_releases_lock_before_llm():
    asyncio.run(_no_lock_across_llm_case())
