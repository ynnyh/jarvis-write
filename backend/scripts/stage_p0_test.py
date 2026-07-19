# backend/scripts/stage_p0_test.py
# -*- coding: utf-8 -*-
"""P0 验证:记忆污染修复(重写章节 → 圣经/伏笔/摘要正确回滚重建)。

用法: .venv/Scripts/python -m scripts.stage_p0_test
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append(ok)
    return ok


def make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def seed(db):
    from app.db.models import Project
    p = Project(title="t", topic="t", genre="g", target_chapters=10,
                target_words_per_chapter=500)
    db.add(p); db.commit()
    return p


# ---------- 1. 圣经清理:删除本章事实 + 重开被取代的旧事实 ----------
def test_bible_purge() -> None:
    from app.db.models import Fact, KnowledgeState
    from app.engines.consistency import BibleService

    db = make_db(); p = seed(db)
    b = BibleService(db, p.id)

    # 第2章:张三受伤(旧事实)
    b.apply_extraction(2, {"fact_changes": [
        {"entity": "张三", "fact_type": "state", "content": "受伤",
         "importance": "major", "replaces": None}]})
    # 第5章:痊愈(取代受伤)+ 新知识状态
    b.apply_extraction(5, {
        "fact_changes": [{"entity": "张三", "fact_type": "state", "content": "痊愈",
                          "importance": "major", "replaces": "受伤"}],
        "knowledge_updates": [{"fact": "痊愈", "knower": "reader", "state": "known"}],
    })
    db.commit()

    injured = db.query(Fact).filter_by(content="受伤").first()
    check("清理前: 受伤已被关区间", injured.valid_until == 4)
    check("清理前: 知识状态存在", db.query(KnowledgeState).count() == 1)

    # 重写第5章 → 清理
    stats = b.purge_chapter_extraction(5)
    db.commit()
    injured = db.query(Fact).filter_by(content="受伤").first()
    ok = (stats["facts_removed"] == 1 and stats["knowledge_removed"] == 1
          and stats["facts_reopened"] == 1
          and db.query(Fact).filter_by(content="痊愈").first() is None
          and injured.valid_until is None
          and db.query(KnowledgeState).count() == 0)
    check("圣经清理: 删本章事实+删知识+重开旧事实", ok, str(stats))


# ---------- 2. 伏笔清理:删埋设 / 撤回收 / 去强化 ----------
def test_foreshadow_purge() -> None:
    from app.db.models import Foreshadowing
    from app.engines.consistency import ForeshadowScheduler

    db = make_db(); p = seed(db)
    s = ForeshadowScheduler(db, p.id)

    s.apply_ops(1, [{"op": "plant", "description": "老伏笔", "expected_payoff_chapter": 6}])
    s.apply_ops(3, [
        {"op": "plant", "description": "第3章新埋的伏笔"},
        {"op": "reinforce", "description": "老伏笔"},
    ])
    s.apply_ops(3, [{"op": "payoff", "description": "老伏笔"}])
    db.commit()

    old = db.query(Foreshadowing).filter_by(description="老伏笔").first()
    check("清理前: 老伏笔已回收", old.status == "paid_off" and old.payoff_chapter == 3)

    stats = s.purge_chapter_ops(3)
    db.commit()
    old = db.query(Foreshadowing).filter_by(description="老伏笔").first()
    ok = (stats["deleted"] == 1
          and db.query(Foreshadowing).filter_by(description="第3章新埋的伏笔").first() is None
          and old.status == "planted"           # 强化记录也被去掉,回落 planted
          and old.payoff_chapter is None
          and old.reinforcement_chapters == [])
    check("伏笔清理: 删埋设+撤回收+去强化", ok, str(stats))


# ---------- 3. 重抽幂等:同章抽两次不产生重复 ----------
async def test_idempotent_extract() -> None:
    from app.db.models import Fact, Foreshadowing
    from app.engines.consistency import extractor as ex_mod

    db = make_db(); p = seed(db)

    reply = ('{"new_entities": [{"name": "张三", "entity_type": "character", "aliases": [], "note": ""}],'
             '"fact_changes": [{"entity": "张三", "fact_type": "state", "content": "断臂",'
             '"importance": "critical", "replaces": null}],'
             '"foreshadow_ops": [{"op": "plant", "description": "断臂之谜", '
             '"expected_payoff_chapter": 8, "importance": "major"}],'
             '"knowledge_updates": []}')

    class A:
        async def ask(self, prompt, system=None): return reply

    with patch.object(ex_mod, "get_adapter_for", return_value=A()):
        await ex_mod.extract_and_apply(db, p.id, 3, "正文v1")
        db.commit()
        n1 = (db.query(Fact).count(), db.query(Foreshadowing).count())
        # 模拟重写后再抽同一章
        await ex_mod.extract_and_apply(db, p.id, 3, "正文v2(重写)")
        db.commit()
        n2 = (db.query(Fact).count(), db.query(Foreshadowing).count())

    check("重抽幂等: 事实/伏笔数不翻倍", n1 == n2 == (1, 1), f"{n1} -> {n2}")


# ---------- 4. 摘要链重建 ----------
async def test_summary_rebuild() -> None:
    from app.db.models import Chapter, ChapterSummary, Outline
    from app.engines.pipeline import chapter as ch_mod

    db = make_db(); p = seed(db)
    for n in (1, 2, 3):
        db.add(Outline(project_id=p.id, chapter_number=n, title=f"章{n}",
                       content_hash=f"h{n}", current_version=1))
        db.add(Chapter(project_id=p.id, chapter_number=n,
                       final_content=f"第{n}章正文", word_count=100, status="finalized"))
        db.add(ChapterSummary(project_id=p.id, chapter_number=n,
                              rolling_summary=f"旧摘要{n}"))
    db.commit()

    calls = []

    class A:
        async def ask(self, prompt, system=None):
            calls.append(prompt)
            return f"新摘要(第{len(calls)}次重建)"

    with patch.object(ch_mod, "get_adapter_for", return_value=A()):
        rebuilt = await ch_mod.rebuild_summaries_after(db, p, 1)
        db.commit()

    s2 = db.query(ChapterSummary).filter_by(chapter_number=2).first()
    s3 = db.query(ChapterSummary).filter_by(chapter_number=3).first()
    ok = (rebuilt == [2, 3] and len(calls) == 2
          and "新摘要" in s2.rolling_summary and "新摘要" in s3.rolling_summary)
    check("摘要链重建: 第1章重写→重算2、3章", ok, f"rebuilt={rebuilt}")

    # 链式传递:重建第3章时用的应是"新的"第2章摘要
    check("摘要链重建: 顺序传递新摘要", "新摘要(第1次重建)" in calls[1])

    # 无下游时不调用
    calls.clear()
    with patch.object(ch_mod, "get_adapter_for", return_value=A()):
        rebuilt = await ch_mod.rebuild_summaries_after(db, p, 3)
    check("摘要链重建: 末章重写无需重建", rebuilt == [] and not calls)


def main() -> int:
    print("=" * 56)
    print("P0 验证:记忆污染修复(圣经/伏笔/摘要)")
    print("=" * 56)
    test_bible_purge()
    test_foreshadow_purge()
    asyncio.run(test_idempotent_extract())
    asyncio.run(test_summary_rebuild())
    print("-" * 56)
    passed, total = sum(results), len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
