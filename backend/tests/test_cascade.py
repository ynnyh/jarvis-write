# tests/test_cascade.py
# -*- coding: utf-8 -*-
"""级联重生成回归(mock LLM,无需 API key)。填补 cascade 此前的测试盲区。

锁住两条纪律:
1. 不得「拿着读快照跨 LLM 调用」—— 与 extract_and_apply 同类的 database is locked
   病根。cascade 读 source/邻章大纲后若不提交就发 LLM,LLM 期间别的连接一提交,
   回写时它那份过期读快照升级写锁即失败(WAL 下 SQLITE_BUSY_SNAPSHOT 不走
   busy_timeout,直接报 database is locked)。修复:LLM 前 commit、每章写完 commit。
2. 重生成结果正确落库(updated 列表 + 大纲字段更新 + 已有正文标记 stale)。

用真·文件库 + 探针适配器:ask() 期间用另一条独立连接提交一次写,制造快照冲突条件。
  修复前 → cascade LLM 后回写撞 database is locked,cascade_regenerate 抛错;
  修复后 → LLM 前已 commit,回写用新快照,顺利完成。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


# parse_blueprint 吃「第N章 - 标题 / 字段:值」文本格式(非 JSON)
_BLUEPRINT_TEXT = (
    "第2章 - 改后的标题\n"
    "本章简述:上游变动后的新剧情走向\n"
    "本章定位:承接转折\n"
)


async def _cascade_case() -> None:
    from sqlalchemy import text

    from app.db.base import Base
    import app.db.models  # noqa: F401 — 注册全部表
    from app.db.models import Chapter, Outline, Project
    from app.db.session import SessionLocal, engine
    from app.engines.cascade import regenerate as regen_mod

    Base.metadata.create_all(engine)

    setup = SessionLocal()
    proj = Project(title="cascade-test", target_chapters=3)
    setup.add(proj)
    setup.flush()
    pid = proj.id
    for n in (1, 2, 3):
        setup.add(Outline(
            project_id=pid, chapter_number=n, title=f"第{n}章", summary=f"第{n}章梗概",
            foreshadowing="无", content_hash="", current_version=1,
        ))
    setup.flush()
    o2 = (
        setup.query(Outline)
        .filter(Outline.project_id == pid, Outline.chapter_number == 2)
        .first()
    )
    # 第 2 章已有正文 → 重生成后应被标记 stale
    setup.add(Chapter(
        project_id=pid, outline_id=o2.id, chapter_number=2,
        draft_content="正文", final_content="第 2 章已有正文", word_count=6,
        status="approved",
    ))
    setup.commit()
    setup.close()

    inner: dict = {"ok": None, "err": None}

    class _ProbeAdapter:
        """LLM 调用期间用另一条连接提交一次写,制造快照冲突条件。"""

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
            return _BLUEPRINT_TEXT

    sa = SessionLocal()
    proj2 = sa.get(Project, pid)
    with patch.object(regen_mod, "get_adapter_for", return_value=_ProbeAdapter()):
        # 修复前:cascade 在 LLM 后回写时会因过期快照撞 database is locked,这里抛错。
        result = await regen_mod.cascade_regenerate(
            sa, proj2, source_chapter=1, chapter_numbers=[2],
        )
    sa.close()

    # 探针写本身在 WAL 下不被读快照阻塞,主要作用是制造「LLM 期间库已变」的冲突条件;
    # 真正的回归守卫是上面 cascade_regenerate 没有抛 database is locked 且下面落库正确。
    assert inner["ok"] is True, f"探针写失败(环境异常):{inner['err']}"

    assert result["updated"] == [2], f"应更新第 2 章,实际 {result['updated']}"
    assert result["stale_chapters"] == [2], (
        f"第 2 章有正文,重生成后应标 stale,实际 {result['stale_chapters']}"
    )

    check = SessionLocal()
    o2_after = (
        check.query(Outline)
        .filter(Outline.project_id == pid, Outline.chapter_number == 2)
        .first()
    )
    ch2_after = (
        check.query(Chapter)
        .filter(Chapter.project_id == pid, Chapter.chapter_number == 2)
        .first()
    )
    assert o2_after.title == "改后的标题", f"大纲标题应更新,实际 {o2_after.title!r}"
    assert o2_after.current_version == 2, "大纲版本应 +1"
    assert ch2_after.is_stale is True and ch2_after.status == "stale", "已有正文应标 stale"
    check.close()


def test_cascade_regenerate_releases_lock_and_updates():
    asyncio.run(_cascade_case())
