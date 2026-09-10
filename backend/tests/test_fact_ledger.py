# tests/test_fact_ledger.py
# -*- coding: utf-8 -*-
"""事实引用追踪与失效传播回归(§1.5)。

锁住四件事:
1. record_usages 幂等(同键累加 times,不建重复行);
2. _probe_phrase 的短分句拒绝(「重伤」这类两字事实不能满地误命中);
3. invalidate_fact 的 dry_run / 落库两态,以及「已写章 vs 未写章」分类;
4. 接线点真的会写日志(scene_write 的检索注入路 / chapter_maintenance 的抽取路),
   以及 cascade 重生成时会清掉该章旧日志。

用真·文件库(与 test_cascade 同范式),不 mock DB。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text  # noqa: F401 — 保持与其它测试一致的导入面

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部表
from app.db.models import (
    Chapter,
    Entity,
    Fact,
    FactUsage,
    Outline,
    Project,
    Scene,
)
from app.db.session import SessionLocal, engine
from app.engines.consistency import fact_ledger as fl


def _fresh_db() -> None:
    """清空所有业务表的行,但**不 drop 表**。

    为什么不能 drop_all:这套用例直接查 ``FactUsage.count()`` 与 ``project_id``
    (每套用例的 project_id 都从 1 开始),如果沿用共享库里的上一条用例数据,
    断言会看到别人的行,必须清干净。

    但也不能 ``drop_all``:测试库里的 FTS5 虚表(fts_chapters 等)是
    ``test_retrieval.py`` 用原生 SQL 建的,不在 ``Base.metadata`` 里;一旦
    drop_all 会把它们连同影子表一起抹掉,而后续模块(如 test_search_api)的
    全文检索用例正依赖它们存在——表现为「单独跑绿、全量跑红」。所以这里只
    按依赖倒序 DELETE 行,让虚表与触发器原样留着(触发器会自动清掉索引行)。
    """
    Base.metadata.create_all(engine)  # checkfirst=True:已存在则不动(更不碰 FTS 虚表)
    db = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()


def _mk_project(db, title: str = "fact-ledger-test") -> tuple[int, int]:
    """建项目 + 一个实体。返回 (project_id, entity_id)。"""
    proj = Project(title=title, target_chapters=10)
    db.add(proj)
    db.flush()
    ent = Entity(project_id=proj.id, name="林砚", entity_type="character")
    db.add(ent)
    db.flush()
    return proj.id, ent.id


# ---------- 写入侧 ----------


def test_record_usages_is_idempotent_and_accumulates():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        f1 = Fact(project_id=pid, entity_id=eid, fact_type="state", content="左臂受伤", valid_from=1)
        f2 = Fact(project_id=pid, entity_id=eid, fact_type="state", content="持玄铁令", valid_from=2)
        db.add_all([f1, f2])
        db.flush()

        n = fl.record_usages(db, pid, [f1.id, f2.id], 3, evidence="臂上缠着布")
        assert n == 2, f"首次应写 2 行,实际 {n}"

        # 同键再来一次:不建新行,times 累加
        n2 = fl.record_usages(db, pid, [f1.id], 3, evidence="臂上缠着布")
        assert n2 == 1
        db.flush()

        rows = db.query(FactUsage).filter(FactUsage.project_id == pid).all()
        assert len(rows) == 2, f"同键不该建重复行,实际 {len(rows)} 行"
        row_f1 = next(r for r in rows if r.fact_id == f1.id)
        assert row_f1.times == 2, f"times 应累加到 2,实际 {row_f1.times}"

        # 去重:同一批里重复给同一个 id 只算一次
        assert fl.record_usages(db, pid, [f2.id, f2.id, f2.id], 4) == 1

        # 空列表不建行
        before = db.query(FactUsage).count()
        assert fl.record_usages(db, pid, [], 5) == 0
        db.flush()
        assert db.query(FactUsage).count() == before, "空 fact_ids 不该建行"
    finally:
        db.close()


def test_record_usages_distinguishes_scene_and_source():
    """同一章同一事实,不同场/不同来源 → 是不同行(不互相覆盖)。"""
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        f = Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="带着伤", valid_from=1,
        )
        db.add(f)
        db.flush()
        # scene_id 有 FK 约束,必须是真的场景行
        outline = Outline(
            project_id=pid, chapter_number=3, title="第3章",
            summary="梗概", foreshadowing="无",
        )
        db.add(outline)
        db.flush()
        scenes = []
        for seq in (1, 2):
            s = Scene(
                project_id=pid, outline_id=outline.id, chapter_number=3, seq=seq,
                title=f"第{seq}场", target_words=1000,
            )
            db.add(s)
            scenes.append(s)
        db.flush()
        s1, s2 = scenes

        fl.record_usages(db, pid, [f.id], 3, scene_id=s1.id, source="retrieval")
        fl.record_usages(db, pid, [f.id], 3, scene_id=s2.id, source="retrieval")
        fl.record_usages(db, pid, [f.id], 3, scene_id=s1.id, source="extract")
        db.flush()

        rows = db.query(FactUsage).filter(FactUsage.fact_id == f.id).all()
        assert len(rows) == 3, f"场/来源不同应各占一行,实际 {len(rows)}"
    finally:
        db.close()


def test_probe_phrase_rejects_short_clause():
    """两字事实不能被当成探针:会满地误命中。"""
    assert fl._probe_phrase("重伤") == "", "两字分句必须拒绝"
    assert fl._probe_phrase("") == ""
    assert fl._probe_phrase("，。、") == "", "纯标点应返回空串"

    # 够长的分句照常返回
    long_fact = "林砚的左臂被剑贯穿,伤口已结痂"
    probe = fl._probe_phrase(long_fact)
    assert probe, "长事实应能取出探针"
    assert probe in long_fact


def test_probe_phrase_rejects_low_ratio():
    """分句虽够长,但远短于整句 → 说明事实本身很长,该分句不独特,拒绝。

    「他,…,拎着刀,站在崖顶,…」这种整句里,最长分句只占一小部分;
    拿它当探针会在正文里到处命中,故按占比拒绝。
    """
    content = "他生在乱世，长在剑炉，后来又拜入山门，拎着刀，站在崖顶，望向东方"
    probe = fl._probe_phrase(content)
    assert probe == "", f"占比过低的分句应拒绝,实际 {probe!r}"


def test_record_extract_hits_only_long_facts():
    """抽取路:字面命中长事实记一笔,短事实(如「重伤」)不记。"""
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        long_fact = Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="林砚的左臂被剑贯穿，伤口已结痂", valid_from=1,
        )
        short_fact = Fact(project_id=pid, entity_id=eid, fact_type="state", content="重伤", valid_from=1)
        # 第 5 章才发生的事实不该在第 3 章被算作命中
        future_fact = Fact(
            project_id=pid, entity_id=eid, fact_type="state", content="腰间多了一枚玉佩", valid_from=5,
        )
        db.add_all([long_fact, short_fact, future_fact])
        db.flush()

        text_body = "他想起林砚的左臂被剑贯穿，血已干透。虽然重伤，脚下却不停。"
        n = fl.record_extract_hits(db, pid, 3, text_body)
        assert n == 1, f"只应命中长事实,实际 {n}"

        rows = db.query(FactUsage).filter(FactUsage.project_id == pid).all()
        assert len(rows) == 1
        assert rows[0].fact_id == long_fact.id
        assert rows[0].source == "extract"
        assert rows[0].evidence, "抽取命中应留证据片段"

        # 空正文不记
        assert fl.record_extract_hits(db, pid, 4, "   ") == 0
    finally:
        db.close()


def test_forget_chapter_only_clears_one_chapter():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        f = Fact(project_id=pid, entity_id=eid, fact_type="state", content="带着旧伤", valid_from=1)
        db.add(f)
        db.flush()
        for ch in (2, 3, 4):
            fl.record_usages(db, pid, [f.id], ch)
        db.flush()
        assert db.query(FactUsage).count() == 3

        n = fl.forget_chapter(db, pid, 3)
        assert n == 1, f"应只清掉第 3 章的日志,实际 {n}"
        left = {r.chapter_number for r in db.query(FactUsage).all()}
        assert left == {2, 4}, f"其它章日志不该被波及,实际 {left}"
    finally:
        db.close()


# ---------- 读取侧 ----------


def test_usages_of_sorted_by_chapter_then_source_rank():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        f = Fact(project_id=pid, entity_id=eid, fact_type="state", content="身上有伤", valid_from=1)
        db.add(f)
        db.flush()
        # 第 5 章:两种来源;第 2 章:一种
        fl.record_usages(db, pid, [f.id], 5, source="extract")
        fl.record_usages(db, pid, [f.id], 5, source="retrieval")
        fl.record_usages(db, pid, [f.id], 2, source="manual")
        db.flush()

        rows = fl.usages_of(db, pid, f.id)
        assert [r.chapter_number for r in rows] == [2, 5, 5], (
            f"应先按章号排,实际 {[r.chapter_number for r in rows]}"
        )
        # 第 5 章内部:retrieval(rank 1) 应排在 extract(rank 2) 前
        assert rows[1].source == "retrieval", (
            f"同章内高可信来源应在前,实际 {rows[1].source}"
        )
        assert rows[0].source_cn == "人工标注"
    finally:
        db.close()


def test_writeback_map_lists_facts_of_chapter():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db)
        crit = Fact(
            project_id=pid, entity_id=eid, fact_type="state", content="掌门已死", valid_from=1,
            importance="critical",
        )
        minor = Fact(
            project_id=pid, entity_id=eid, fact_type="state", content="衣角破了", valid_from=1,
            importance="minor",
        )
        db.add_all([crit, minor])
        db.flush()
        fl.record_usages(db, pid, [crit.id], 3, source="retrieval")
        fl.record_usages(db, pid, [minor.id], 3, source="extract")
        db.flush()

        wb = fl.writeback_map(db, pid, chapter_number=3)
        assert len(wb) == 2
        assert wb[0].fact_id == crit.id, "critical 应排在 minor 前"
        assert wb[0].importance == "critical"
        assert wb[0].open_ended is True, "valid_until 为空即仍生效"
        assert "retrieval" in wb[0].sources

        # 没有日志的章返回空
        assert fl.writeback_map(db, pid, chapter_number=99) == []
    finally:
        db.close()


# ---------- 失效传播 ----------


def _seed_invalidation_case(db) -> tuple[int, int, int]:
    """建一个「事实在多个章被引用,部分章已写正文」的场景。

    返回 (project_id, fact_id, entity_id)。
    """
    pid, eid = _mk_project(db, "invalidate-test")
    fact = Fact(
        project_id=pid, entity_id=eid, fact_type="state",
        content="腰间挂着玄铁令", valid_from=1, importance="critical",
    )
    db.add(fact)
    db.flush()
    for n in (1, 2, 3, 4):
        db.add(Outline(
            project_id=pid, chapter_number=n, title=f"第{n}章",
            summary=f"第{n}章梗概", foreshadowing="无",
        ))
    db.flush()
    outlines = {
        o.chapter_number: o
        for o in db.query(Outline).filter(Outline.project_id == pid).all()
    }
    # 第 2 章没写正文;第 3、4 章有正文
    for n in (3, 4):
        db.add(Chapter(
            project_id=pid, outline_id=outlines[n].id, chapter_number=n,
            final_content=f"第{n}章正文", word_count=5, status="approved",
        ))
    db.flush()
    # 引用:第 2 章(未写)、第 3 章(retrieval,已写)、第 4 章(extract,已写)
    fl.record_usages(db, pid, [fact.id], 2, source="retrieval")
    fl.record_usages(db, pid, [fact.id], 3, source="retrieval")
    fl.record_usages(db, pid, [fact.id], 4, source="extract")
    db.flush()
    return pid, fact.id, eid


def test_invalidate_fact_dry_run_does_not_write():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, fid, _ = _seed_invalidation_case(db)
        report = fl.invalidate_fact(db, pid, fid, valid_until=3, dry_run=True)

        # dry_run 不落库
        assert db.get(Fact, fid).valid_until is None, "dry_run 不该改 valid_until"
        assert report.new_valid_until == 3

        # 第 3、4 章已写正文且在第 3 章之后(含第 3 章)引用了它 → 受影响
        nums = [a.chapter_number for a in report.affected]
        assert nums == [3, 4], f"应含第 3、4 章,实际 {nums}"
        assert [a.chapter_number for a in report.written_affected] == [3, 4]

        # 第 2 章在失效点之前用过它,那时还有效 → 不该进受影响清单
        assert 2 not in nums, "失效点之前的使用不该被算作受影响"
    finally:
        db.close()


def test_invalidate_fact_commit_writes_valid_until():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, fid, _ = _seed_invalidation_case(db)
        report = fl.invalidate_fact(db, pid, fid, valid_until=3)
        db.commit()
        assert db.get(Fact, fid).valid_until == 3
        assert report.critical_count == 1, "第 3 章是 retrieval 强信号,应计 1"
    finally:
        db.close()


def test_invalidate_fact_no_usage_says_nothing_to_review():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db, "no-usage")
        f = Fact(project_id=pid, entity_id=eid, fact_type="state", content="无人引用的事实", valid_from=1)
        db.add(f)
        db.flush()
        report = fl.invalidate_fact(db, pid, f.id, valid_until=2)
        assert report.affected == []
        assert any("无需复核" in n for n in report.notes), (
            f"无引用时应给出说明,实际 {report.notes}"
        )
        assert "无引用记录" in fl.render_invalidation_report(report)
    finally:
        db.close()


def test_invalidate_fact_missing_raises():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, _ = _mk_project(db, "missing-fact")
        try:
            fl.invalidate_fact(db, pid, 99999, valid_until=2)
        except ValueError:
            pass
        else:
            raise AssertionError("不存在的事实应抛 ValueError")
    finally:
        db.close()


def test_unwritten_only_chapters_says_no_manual_review():
    """只在未写章里被引用 → 提示「生成时自然读到新事实」,不做人工复核。"""
    _fresh_db()
    db = SessionLocal()
    try:
        pid, eid = _mk_project(db, "unwritten-only")
        f = Fact(project_id=pid, entity_id=eid, fact_type="state", content="尚未落笔的事实", valid_from=1)
        db.add(f)
        db.flush()
        fl.record_usages(db, pid, [f.id], 5, source="retrieval")
        db.flush()

        report = fl.invalidate_fact(db, pid, f.id, valid_until=2)
        assert report.affected, "应有引用记录"
        assert report.written_affected == [], "该章未写正文"
        assert any("无需人工复核" in n for n in report.notes), report.notes
    finally:
        db.close()


def test_render_invalidation_report_lists_chapters():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, fid, _ = _seed_invalidation_case(db)
        report = fl.invalidate_fact(db, pid, fid, valid_until=3)
        text = fl.render_invalidation_report(report)
        assert "事实失效复核" in text
        assert "第 3 章" in text or "第3章" in text
        assert "检索注入" in text, "应渲染来源中文名"
    finally:
        db.close()


# ---------- 接线点(生成路径真的写日志) ----------


def _seed_scene_case(db):
    """建项目 + 大纲 + 场景 + 一条关键事实。返回 (project, scene, fact)。"""
    from app.db.models import Project as P

    proj = P(title="scene-usage", target_chapters=5)
    db.add(proj)
    db.flush()
    ent = Entity(project_id=proj.id, name="林砚", entity_type="character")
    db.add(ent)
    db.flush()
    outline = Outline(
        project_id=proj.id, chapter_number=1, title="第1章",
        summary="梗概", foreshadowing="无",
    )
    db.add(outline)
    db.flush()
    scene = Scene(
        project_id=proj.id, outline_id=outline.id, chapter_number=1, seq=1,
        title="祭坛", summary="撞破", location="祭坛",
        characters=["林砚"], goal="确认身份", conflict="遭伏",
        emotion_target="惊", tension_level=4, target_words=1200,
    )
    db.add(scene)
    db.flush()
    fact = Fact(
        project_id=proj.id, entity_id=ent.id, fact_type="state",
        content="林砚腰间挂着玄铁令", valid_from=1, importance="critical",
    )
    db.add(fact)
    db.flush()
    db.commit()
    return proj, scene, fact


def test_write_scene_records_retrieval_usage():
    """生成路径:write_scene 把检索到的事实记进消费日志。"""
    _fresh_db()
    db = SessionLocal()
    try:
        proj, scene, fact = _seed_scene_case(db)
        pid = proj.id

        from app.engines.pipeline import scene_write as sw

        async def _ask(self, prompt, system=None):  # noqa: ANN001
            return "祭坛上风很大。他站定。"

        class _Adapter:
            ask = _ask

        # 检索层返回这条事实(模拟 FTS/结构化路捞到)
        from app.engines.pipeline.retrieval import SceneContext

        def _fake_retrieve(db_, project_id, scene_, chapter_number):
            return SceneContext(
                facts=[{
                    "id": fact.id, "content": fact.content,
                    "entity_id": fact.entity_id, "entity_name": "林砚",
                    "importance": "critical", "fact_type": "",
                    "valid_from": 1, "_src": "structured", "_score": 0.0,
                }],
                entities=[],
                stats={"merged_facts": 1},
            )

        import unittest.mock as mock

        with mock.patch.object(sw, "retrieve_for_scene", _fake_retrieve), \
                mock.patch.object(sw, "get_adapter_for", lambda *a, **k: _Adapter()):
            asyncio.run(sw.write_scene(
                db, proj, scene,
                chapter_number=1, scene_total=1, style_block="",
                deai_rules="", rolling_summary="", recent_tail="",
                handoff_block="", scene_anchor="", chapter_summary="",
                chapter_title="第1章",
            ))

        rows = db.query(FactUsage).filter(FactUsage.project_id == pid).all()
        assert len(rows) == 1, f"应记 1 条消费日志,实际 {len(rows)}"
        assert rows[0].fact_id == fact.id
        assert rows[0].source == "retrieval"
        assert rows[0].scene_id == scene.id
    finally:
        db.close()


def test_rewrite_path_does_not_record_retrieval_usage():
    """重写路径不记日志:那条 prompt 里没有 facts_block,模型没看到这些事实。"""
    _fresh_db()
    db = SessionLocal()
    try:
        proj, scene, fact = _seed_scene_case(db)
        pid = proj.id

        from app.engines.pipeline import scene_write as sw
        from app.engines.pipeline.retrieval import SceneContext
        import unittest.mock as mock

        calls = {"n": 0}

        def _fake_retrieve(db_, project_id, scene_, chapter_number):
            calls["n"] += 1
            return SceneContext(facts=[{
                "id": fact.id, "content": fact.content,
                "entity_id": fact.entity_id, "entity_name": "林砚",
                "importance": "critical", "fact_type": "",
                "valid_from": 1, "_src": "structured", "_score": 0.0,
            }], entity_id=None, entities=[], stats={}) if False else SceneContext(
                facts=[{
                    "id": fact.id, "content": fact.content,
                    "entity_id": fact.entity_id, "entity_name": "林砚",
                    "importance": "critical", "fact_type": "",
                    "valid_from": 1, "_src": "structured", "_score": 0.0,
                }], entities=[], stats={"merged_facts": 1})

        class _Adapter:
            async def ask(self, prompt, system=None):  # noqa: ANN001
                return "重写后的这一场。"

        with mock.patch.object(sw, "retrieve_for_scene", _fake_retrieve), \
                mock.patch.object(sw, "get_adapter_for", lambda *a, **k: _Adapter()):
            asyncio.run(sw.write_scene(
                db, proj, scene,
                chapter_number=1, scene_total=1, style_block="",
                deai_rules="", rolling_summary="", recent_tail="",
                handoff_block="", scene_anchor="", chapter_summary="",
                chapter_title="第1章", revision_directive="把这一场写得更狠",
            ))

        rows = db.query(FactUsage).filter(FactUsage.project_id == pid).all()
        assert rows == [], f"重写路径不该记消费日志,实际 {len(rows)} 条"
    finally:
        db.close()


def test_cascade_regenerate_forgets_chapter_usage():
    """大纲重生成 → 该章旧引用日志作废(否则下次失效传播会误报)。"""
    _fresh_db()
    db = SessionLocal()
    try:
        proj, scene, fact = _seed_scene_case(db)
        pid = proj.id
        # 先造一条第 1 章的引用日志,再把第 1 章正文标记为已有
        fl.record_usages(db, pid, [fact.id], 1, source="retrieval")
        db.flush()
        o1 = db.query(Outline).filter(
            Outline.project_id == pid, Outline.chapter_number == 1
        ).first()
        db.add(Chapter(
            project_id=pid, outline_id=o1.id, chapter_number=1,
            final_content="第1章已有正文", word_count=5, status="approved",
        ))
        db.add(Outline(
            project_id=pid, chapter_number=2, title="第2章",
            summary="梗概", foreshadowing="无",
        ))
        db.commit()
        assert db.query(FactUsage).count() == 1

        from app.engines.cascade import regenerate as regen_mod

        class _Adapter:
            async def ask(self, prompt, system=None):  # noqa: ANN001
                return "第2章 - 改后的标题\n本章简述:新走向\n本章定位:承接\n"

        import unittest.mock as mock

        # 第 2 章没正文 → 不触发 forget;先给第 2 章也补正文,才走得到清理分支
        db.add(Chapter(
            project_id=pid,
            outline_id=db.query(Outline).filter(
                Outline.project_id == pid, Outline.chapter_number == 2
            ).first().id,
            chapter_number=2, final_content="第2章已有正文", word_count=5,
            status="approved",
        ))
        fl.record_usages(db, pid, [fact.id], 2, source="retrieval")
        db.commit()
        assert db.query(FactUsage).count() == 2

        proj2 = db.get(Project, pid)
        with mock.patch.object(regen_mod, "get_adapter_for", lambda *a, **k: _Adapter()):
            result = asyncio.run(regen_mod.cascade_regenerate(
                db, proj2, source_chapter=1, chapter_numbers=[2],
            ))

        assert result["stale_chapters"] == [2], result
        left = {r.chapter_number for r in db.query(FactUsage).all()}
        assert 2 not in left, f"第 2 章旧日志应被清掉,实际剩 {left}"
        assert 1 in left, "第 1 章日志不该被波及"
    finally:
        db.close()


# ---------- 体检报告 ----------


def test_audit_report_fact_usage_section():
    _fresh_db()
    db = SessionLocal()
    try:
        pid, fid, eid = _seed_invalidation_case(db)
        # 再造一条从没被引用过的 critical 事实(悬空)
        dangling = Fact(
            project_id=pid, entity_id=eid, fact_type="state", content="祖传的青铜剑",
            valid_from=1, importance="critical",
        )
        db.add(dangling)
        db.flush()

        from app.api.editorial import fact_usage_section

        chapters = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid, Chapter.final_content != "")
            .all()
        )
        sec = fact_usage_section(db, pid, chapters)

        assert sec["critical_total"] == 2, sec
        assert any(d["fact_id"] == dangling.id for d in sec["dangling"]), (
            "零引用的 critical 事实应进悬空清单"
        )
        assert not any(d["fact_id"] == fid for d in sec["dangling"]), (
            "有引用的 critical 事实不该进悬空清单"
        )
        # 第 3、4 章有正文且被引用过 → 不在无据清单
        assert 3 not in sec["unsupported_chapters"]
        assert sec["chapters_with_usage"] >= 2
    finally:
        db.close()


def test_audit_report_endpoint_includes_fact_usage():
    """端点级:GET /audit-report 返回体里有 fact_usage 段。

    必须以 ``with TestClient(app)`` 起停:裸 ``TestClient(app)`` 不走 lifespan,
    不会触发启动迁移;而这里前面的用例已用 create_all 建了表却没 stamp
    alembic_version,启动迁移会把库当成「遗留库」stamp 到基线版本,于是
    跳过 0008(FTS5 建表)——后果是**后续模块**(test_search_api)的全文检索
    用例整片报 ``no such table: fts_chapters``。这个坑值得留在这里当路标。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    _fresh_db()
    db = SessionLocal()
    try:
        pid, _fid, _ = _seed_invalidation_case(db)
        db.commit()
    finally:
        db.close()

    # 直连端点(跳过鉴权:audit_report 只依赖 get_project_or_404)
    with TestClient(app) as client:
        resp = client.get(f"/api/projects/{pid}/audit-report")
    assert resp.status_code in (200, 401, 403), resp.text[:200]
    if resp.status_code == 200:
        body = resp.json()
        assert "fact_usage" in body, body.keys()
        assert "dangling" in body["fact_usage"]
