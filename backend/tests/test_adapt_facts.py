# tests/test_adapt_facts.py
# -*- coding: utf-8 -*-
"""改编线继承事实层回归(docs/15 §5.1)。

改编此前只吃「正文文本」,不知道圣经的存在——grep check_chapter|BibleService|
blockers_of 在 drama/clips/promo/script 四线全无命中。后果:改编第 30 章时
不知道「主角此时断了左臂、剑在师弟手上」,只能靠正文片段猜。

本文件锁住 `app/engines/adapt.py` 新增的事实层三件事:
  1. `chapter_facts` 取的是**并集最后一章**的时刻(不是最早的章——那会把
     后续章已作废的事实当硬约束);
  2. 时序过滤与退场实体过滤真的生效;
  3. 排序 critical → major → minor + 超限先砍 minor;
  4. 三条改编线的 prompt 模板都留了 `{facts_block}` 占位符,且组装点真的传了。

用**独立内存库**(与 test_bible_resolve 同范式),不碰共享库:`app.db.session` 的
engine 由 conftest 指向临时文件库,若在这里 create_all,会让 app 启动迁移把库
当成「遗留库」stamp 到基线版本、跳过 0008 的 FTS5 建表,毒化后续全文检索用例。
本文件测的 adapt 事实层不需要 FTS,用内存库最干净。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部表
from app.db.models import Chapter, Entity, Fact, Outline, Project
from app.engines import adapt


def _fresh_db():
    """每次一套干净的内存库(SQLite `sqlite://` 是进程内私有,互不干扰)。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed(db, *, title: str = "adapt-facts") -> tuple[int, int]:
    """建项目 + 主角实体 + 4 章大纲。返回 (project_id, entity_id)。"""
    proj = Project(title=title, target_chapters=4)
    db.add(proj)
    db.flush()
    ent = Entity(project_id=proj.id, name="林砚", entity_type="character")
    db.add(ent)
    db.flush()
    for n in (1, 2, 3, 4):
        db.add(Outline(
            project_id=proj.id, chapter_number=n, title=f"第{n}章",
            summary=f"第{n}章梗概", foreshadowing="无",
        ))
    db.flush()
    return proj.id, ent.id


def test_chapter_facts_uses_latest_chapter_moment():
    """关键:取并集**最后一章**的时刻。取最早的章会把已作废的事实当硬约束。"""
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        # 第 1 章:左臂完好(第 3 章作废);第 4 章:左臂已断
        db.add_all([
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="左臂完好", valid_from=1, valid_until=2,
            ),
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="左臂已被斩断", valid_from=3,
            ),
        ])
        db.flush()

        rows = adapt.chapter_facts(db, pid, [1, 2, 3, 4])
        contents = [r["content"] for r in rows]
        assert "左臂已被斩断" in contents, f"第 4 章时刻的当前状态必须在内:{contents}"
        assert "左臂完好" not in contents, (
            f"第 2 章已作废的事实不该出现在第 4 章时刻:{contents}"
        )
    finally:
        db.close()


def test_chapter_facts_respects_valid_from():
    """未来章才发生的事实不能提前出现(按最后一章的时刻取,同样是时序过滤)。"""
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="腰间挂着玄铁令", valid_from=4,
        ))
        db.flush()
        # 只改编第 1-2 章:第 4 章的事实此刻还没发生
        rows = adapt.chapter_facts(db, pid, [1, 2])
        assert rows == [], f"第 4 章的事实不该出现在第 2 章时刻:{rows}"
    finally:
        db.close()


def test_chapter_facts_excludes_retired_entities():
    """退场角色的事实不再注入(与检索层/硬约束块同一口径)。"""
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        gone = Entity(
            project_id=pid, name="老王", entity_type="character", retired=True,
        )
        db.add(gone)
        db.flush()
        db.add_all([
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="林砚带着伤", valid_from=1,
            ),
            Fact(
                project_id=pid, entity_id=gone.id, fact_type="state",
                content="老王已死", valid_from=1,
            ),
        ])
        db.flush()
        rows = adapt.chapter_facts(db, pid, [1])
        contents = [r["content"] for r in rows]
        assert "林砚带着伤" in contents
        assert "老王已死" not in contents, f"退场角色的事实不该注入:{contents}"
    finally:
        db.close()


def test_chapter_facts_orders_by_importance():
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add_all([
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="衣角破了", valid_from=1, importance="minor",
            ),
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="掌门已死", valid_from=1, importance="critical",
            ),
            Fact(
                project_id=pid, entity_id=eid, fact_type="possession",
                content="持有半块令牌", valid_from=1, importance="major",
            ),
        ])
        db.flush()
        rows = adapt.chapter_facts(db, pid, [1])
        assert [r["importance"] for r in rows] == ["critical", "major", "minor"], rows
    finally:
        db.close()


def test_chapter_facts_truncates_minor_first():
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="最重要的事", valid_from=1, importance="critical",
        ))
        for i in range(5):
            db.add(Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content=f"琐事{i}", valid_from=1, importance="minor",
            ))
        db.flush()
        rows = adapt.chapter_facts(db, pid, [1], limit=3)
        assert len(rows) == 3
        assert rows[0]["importance"] == "critical", "critical 必须保得住"
        assert all(r["importance"] == "minor" for r in rows[1:]), (
            f"超限砍的该是 minor:{[r['importance'] for r in rows]}"
        )
    finally:
        db.close()


def test_facts_block_marks_fidelity_levels():
    """块文本必须区分「必须保真」与「可再创作」——这是 §5.1 待决项的落地口径。"""
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add_all([
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="掌门已死", valid_from=1, importance="critical",
            ),
            Fact(
                project_id=pid, entity_id=eid, fact_type="state",
                content="衣角破了", valid_from=1, importance="minor",
            ),
        ])
        db.flush()
        block = adapt.facts_block(db, pid, [1])
        assert "必须保真" in block and "可再创作" in block, block
        assert "掌门已死" in block
        assert "林砚" in block, "应带实体名(改编要认人)"
    finally:
        db.close()


def test_facts_block_empty_when_no_facts():
    """没有事实 → 空串(整块省略,不留一个空标题污染 prompt)。"""
    db = _fresh_db()
    try:
        pid, _eid = _seed(db)
        assert adapt.facts_block(db, pid, [1]) == ""
        assert adapt.facts_block(db, pid, []) == ""
    finally:
        db.close()


def test_chapter_facts_survives_broken_bible():
    """事实层是增强:内部异常必须吞掉返回空表,不能拖垮改编。"""
    db = _fresh_db()
    try:
        pid, _eid = _seed(db)
        import unittest.mock as mock

        from app.engines.consistency import BibleService

        with mock.patch.object(
            BibleService, "query_facts_at", side_effect=RuntimeError("boom")
        ):
            assert adapt.chapter_facts(db, pid, [1]) == []
    finally:
        db.close()


# ---------- 三条改编线的模板/组装点 ----------


def test_script_adapt_prompt_has_facts_placeholder():
    from app.prompts.script import SCRIPT_ADAPT_PROMPT

    assert "{facts_block}" in SCRIPT_ADAPT_PROMPT
    # 组装点必须传全占位符(少了会 KeyError)
    assert "{assets_block}" in SCRIPT_ADAPT_PROMPT
    assert "{banned_block}" in SCRIPT_ADAPT_PROMPT
    assert "{threads_block}" in SCRIPT_ADAPT_PROMPT
    assert "{source_text}" in SCRIPT_ADAPT_PROMPT


def test_clips_novel_context_prompt_has_facts_placeholder():
    from app.prompts.clips import CLIPS_NOVEL_CONTEXT

    assert "{facts_block}" in CLIPS_NOVEL_CONTEXT
    for key in ("{title}", "{genre}", "{topic}", "{concept_block}",
                "{excerpts_block}", "{characters_block}", "{duration_s}",
                "{direction_directive}", "{steering_block}", "{inspiration_block}"):
        assert key in CLIPS_NOVEL_CONTEXT, key


def test_drama_planner_has_facts_helper():
    """漫剧线的事实块经 _facts_block 收口(与 _banned_block 同一范式)。"""
    from app.engines.drama import planner

    assert hasattr(planner, "_facts_block"), "漫剧线应有 _facts_block 收口函数"
    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="主角左臂已断", valid_from=1, importance="critical",
        ))
        db.flush()
        block = planner._facts_block(db, pid, 1, 2)
        assert "主角左臂已断" in block, block
        # 区间取的是并集最后一章的时刻(2):一条 valid_from=5 的事实此刻还没发生
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="第5章才发生的事", valid_from=5, importance="major",
        ))
        db.flush()
        block2 = planner._facts_block(db, pid, 1, 2)
        assert "第5章才发生的事" not in block2, (
            f"范围内还没发生的事实不该注入:{block2}"
        )
        block3 = planner._facts_block(db, pid, 1, 6)
        assert "第5章才发生的事" in block3, "区间覆盖到第 6 章时应带上它"
    finally:
        db.close()


def test_script_adapt_prompt_formats_with_facts():
    """端到端:模板用真实素材 format 一次,确认没有漏传的占位符。"""
    from app.prompts.script import SCRIPT_ADAPT_PROMPT

    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="掌门已死", valid_from=1, importance="critical",
        ))
        db.flush()
        proj = db.get(Project, pid)
        text = SCRIPT_ADAPT_PROMPT.format(
            title=proj.title, chapter_count=4, target_episodes=6,
            source_text="(正文)",
            assets_block=adapt.book_assets_block(proj),
            banned_block=adapt.banned_block(db, pid),
            threads_block=adapt.open_threads_block(db, pid, [1, 2, 3, 4]),
            facts_block=adapt.facts_block(db, pid, [1, 2, 3, 4]),
        )
        assert "掌门已死" in text
        assert "{" not in text.split("严格输出 JSON")[0].replace("{{", "").replace("}}", ""), (
            "格式串没被完全替换 —— 有漏传的占位符"
        )
    finally:
        db.close()


def test_clips_novel_context_returns_three_parts(monkeypatch=None):
    """_novel_context 的返回契约从 2 元变 3 元(加了 facts)。"""
    from app.engines.clips import batch as clips_batch

    db = _fresh_db()
    try:
        pid, eid = _seed(db)
        from app.db.models import Project as P

        # 造一章定稿正文(状态需 approved 才被 _novel_context 取到)
        out = db.query(Outline).filter(
            Outline.project_id == pid, Outline.chapter_number == 4
        ).first()
        db.add(Chapter(
            project_id=pid, outline_id=out.id, chapter_number=4,
            final_content="风从崖底卷上来。", word_count=8, status="approved",
        ))
        db.add(Fact(
            project_id=pid, entity_id=eid, fact_type="state",
            content="主角左臂已断", valid_from=1, importance="critical",
        ))
        db.flush()

        proj = db.get(P, pid)
        excerpts, characters, facts = clips_batch._novel_context(db, proj)
        assert "风从崖底卷上来" in excerpts
        assert isinstance(characters, str)
        assert "主角左臂已断" in facts, facts
    finally:
        db.close()
