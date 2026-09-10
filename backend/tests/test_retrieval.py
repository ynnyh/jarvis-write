# tests/test_retrieval.py
# -*- coding: utf-8 -*-
"""检索式注入测试(无 LLM,纯 DB + FTS5)。

钉住的核心契约:
1. 给一个场景,只捞它需要的事实——不是全库;
2. 前章正文里与这一场相关的段落要被捞出来(此前只注入最近 2 章尾部,第 3 章埋
   的细节根本进不来);
3. 时序过滤必须在 SQL 层生效(早失效/未生效的事实不许混进本场);
4. critical 事实永不被 minor 挤掉;
5. FTS5 不可用/查询语法炸了 → 回落结构化路,绝不抛异常。
"""
from __future__ import annotations

import pytest

from app.engines.pipeline import retrieval as rt


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _fts(db) -> bool:
    """建 FTS5 虚表(与 0008 迁移同构的最小版)。测试库走 create_all,不跑迁移。

    探测方式:真的建一张试一试——`SELECT fts5(?)` 不是有效调用,
    用 try CREATE 才能准确判断本环境的 SQLite 是否编译了 FTS5。
    """
    from sqlalchemy import text as sql

    try:
        db.execute(sql("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(c, tokenize='trigram')"))
        db.execute(sql("DROP TABLE _fts_probe"))
    except Exception:
        return False
    stmts = [
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_facts USING fts5("
        "content, project_id UNINDEXED, source_chapter UNINDEXED, "
        "entity_id UNINDEXED, tokenize='trigram')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_entities USING fts5("
        "content, project_id UNINDEXED, name UNINDEXED, "
        "entity_type UNINDEXED, tokenize='trigram')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chapters USING fts5("
        "content, project_id UNINDEXED, chapter_number UNINDEXED, tokenize='trigram')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_foreshadowings USING fts5("
        "content, project_id UNINDEXED, chapter_planted UNINDEXED, "
        "status UNINDEXED, tokenize='trigram')",
    ]
    for s in stmts:
        db.execute(sql(s))
    db.commit()
    return True


def _seed_facts(db, project_id: int, entity_id: int, rows: list[tuple]) -> None:
    """直接写 facts 表并手工维护 fts_facts(测试不跑触发器)。"""
    from sqlalchemy import text as sql

    from app.db.models import Fact

    for content, valid_from, valid_until, importance in rows:
        db.add(
            Fact(
                project_id=project_id,
                entity_id=entity_id,
                fact_type="state",
                content=content,
                valid_from=valid_from,
                valid_until=valid_until,
                importance=importance,
                source_chapter=valid_from,
            )
        )
    db.commit()
    for f in db.query(Fact).filter(Fact.project_id == project_id).all():
        db.execute(
            sql(
                "INSERT INTO fts_facts(rowid, content, project_id, source_chapter, entity_id) "
                "VALUES (:r, :c, :p, :s, :e)"
            ),
            {"r": f.id, "c": f.content, "p": f.project_id, "s": f.source_chapter, "e": f.entity_id},
        )
    db.commit()


def _seed_chapter(db, project, number: int, content: str) -> None:
    from sqlalchemy import text as sql

    from app.db.models import Chapter

    ch = Chapter(
        project_id=project.id,
        chapter_number=number,
        final_content=content,
        word_count=len(content),
    )
    db.add(ch)
    db.commit()
    db.execute(
        sql(
            "INSERT INTO fts_chapters(rowid, content, project_id, chapter_number) "
            "VALUES (:r, :c, :p, :n)"
        ),
        {"r": ch.id, "c": content, "p": project.id, "n": number},
    )
    db.commit()


def _fixture():
    from app.db.models import Entity, Project, Scene

    db = _db()
    if not _fts(db):
        pytest.skip("本环境的 SQLite 未编译 FTS5")
    project = Project(
        title="破封纪", topic="修仙", genre="仙侠",
        target_chapters=10, target_words_per_chapter=3000,
    )
    db.add(project)
    db.commit()
    hero = Entity(project_id=project.id, entity_type="character", name="林晚")
    rival = Entity(project_id=project.id, entity_type="character", name="周衍")
    db.add_all([hero, rival])
    db.commit()
    return db, project, hero, rival


def _scene(db, project, **kw):
    from app.db.models import Scene

    fields = dict(
        project_id=project.id,
        outline_id=1,
        chapter_number=6,
        seq=1,
        title="祭坛撞破",
        summary="林晚看见周衍正在祭炼玄铁令,那道疤让他认出了仇人。",
        location="祭坛",
        characters=["林晚", "周衍"],
        goal="确认仇人身份",
        conflict="认出仇人却不能动手",
        emotion_target="灼痛",
        tension_level=5,
        target_words=1500,
        fact_hints=["玄铁令", "旧伤"],
    )
    fields.update(kw)
    s = Scene(**fields)
    db.add(s)
    db.commit()
    return s


# ---------- 查询串构造(纯函数) ----------

def test_build_query_uses_content_fields_not_structure():
    """查询串只取内容性字段:张力档「5」不该进检索。"""
    db, project, _, _ = _fixture()
    scene = _scene(db, project)
    q = rt.build_query(scene)
    assert "祭坛撞破" in q
    assert "玄铁令" in q
    assert "确认仇人身份" in q
    # 2 字词(fact_hints 里的「旧伤」)按 trigram 约束被剔除
    assert "旧伤" not in q
    # 数字型结构字段不进查询
    assert " 5 " not in f" {q} "


def test_build_query_drops_sub_trigram_tokens():
    """**关键契约**:2 字词必须被剔除。

    trigram 分词器把文本切成 3 字符片段,短于 3 字符的词不在倒排表里。
    更糟的是 FTS5 对此不报错、直接返回 0 行——一个 2 字词混进查询串,
    整个 MATCH 静默失效(实测:`祭坛 玄铁令 认出仇人` 命中 0,`玄铁令` 命中 1)。
    """
    db, project, _, _ = _fixture()
    scene = _scene(db, project, summary="他确认祭坛有玄铁令")
    q = rt.build_query(scene)
    for tok in q.split():
        assert len(tok) >= 3, f"短词 {tok!r} 会让整个 MATCH 静默失效"


def test_build_query_dedups_and_drops_stopwords():
    db, project, _, _ = _fixture()
    scene = _scene(db, project, summary="他已经知道了祭坛的秘密秘密")
    q = rt.build_query(scene)
    toks = q.split()
    assert len(toks) == len(set(toks)), "重复词应被去掉"


def test_build_query_handles_empty_scene():
    db, project, _, _ = _fixture()
    scene = _scene(db, project, title="", summary="", goal="", conflict="", fact_hints=[])
    assert rt.build_query(scene) == ""


def test_fts_or_query_requires_min_length():
    assert rt._fts_or_query(["祭坛"]) == ""          # 2 字 → 不可检索
    assert rt._fts_or_query(["玄铁令"]) == "玄铁令"
    assert rt._fts_or_query(["玄铁令", "祭坛"]) == "玄铁令"
    assert " OR " in rt._fts_or_query(["玄铁令", "认出仇人"])


def test_fts_or_query_strips_syntax_chars():
    """含 FTS 语法字符的词必须剔除,否则整个 MATCH 报错或语义错乱。"""
    out = rt._fts_or_query(["玄铁令", '他说"别动"', "冲突*爆发", "^开始"])
    assert out == "玄铁令"


# ---------- 字面路:事实检索 ----------

def test_retrieve_finds_scene_relevant_fact_from_early_chapter():
    """第 3 章埋的「玄铁令」事实,第 6 章写这个场景时必须被捞到。"""
    db, project, hero, rival = _fixture()
    _seed_facts(
        db, project.id, hero.id,
        [
            ("林晚在玄铁令上留下过一道刻痕", 3, None, "critical"),
            ("林晚今天早饭吃了三个包子", 5, None, "minor"),
            ("林晚在城东租了个院子", 2, None, "major"),
        ],
    )
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)

    contents = " ".join(f["content"] for f in ctx.facts)
    assert "玄铁令" in contents, "字面相关的早期事实必须被检索到"
    assert ctx.stats["fts"] is True
    assert ctx.stats["merged_facts"] > 0


def test_retrieve_respects_temporal_validity():
    """时序:第 4 章就结束的伤,写到第 6 章不该再要求「带伤」。"""
    db, project, hero, _ = _fixture()
    _seed_facts(
        db, project.id, hero.id,
        [
            ("林晚左臂有伤", 2, 4, "critical"),      # 第 4 章痊愈 → 第 6 章无效
            ("林晚戴着玄铁戒指", 2, None, "major"),   # 长期有效
            ("林晚将在一场比斗中出场", 8, None, "major"),  # 第 8 章才生效
        ],
    )
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    contents = " ".join(f["content"] for f in ctx.facts)
    assert "左臂有伤" not in contents, "已失效的事实不该注入"
    assert "将在" not in contents, "尚未生效的事实不该注入"
    assert "玄铁戒指" in contents


def test_critical_facts_survive_truncation():
    """事实超上限时,critical 必须留下,minor 先被砍。"""
    db, project, hero, _ = _fixture()
    rows = [("林晚在玄铁令上留下过刻痕", 1, None, "critical")]
    rows += [(f"林晚的第{i}件琐事记录", 1, None, "minor") for i in range(40)]
    _seed_facts(db, project.id, hero.id, rows)
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)

    assert len(ctx.facts) <= rt.MAX_FACTS_PER_SCENE
    assert any(f["importance"] == "critical" for f in ctx.facts), "critical 被挤掉了"


# ---------- 字面路:前章正文片段 ----------

def test_retrieve_pulls_relevant_passage_from_older_chapter():
    """第 2 章正文里提到玄铁令的段落要进上下文(不靠最近 2 章尾部)。"""
    db, project, _, _ = _fixture()
    body = (
        "雨下了整夜。" * 5
        + "他想起玄铁令上那道刻痕,那是他亲手留下的,如今却成了别人祭炼的引子。"
        + "远处传来钟声。" * 5
    )
    _seed_chapter(db, project, 2, body)
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)

    tails = ctx.stats.get("tail_texts") or []
    assert tails, "第 2 章的相关段落没被捞出来"
    assert any("玄铁令" in t for t in tails)


def test_retrieve_ignores_current_and_future_chapters():
    """检索前文片段时不许把本章或未来章算进来。"""
    db, project, _, _ = _fixture()
    _seed_chapter(db, project, 6, "玄铁令出现在本章。" * 10)
    _seed_chapter(db, project, 9, "玄铁令出现在未来的章。" * 10)
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    tails = ctx.stats.get("tail_texts") or []
    assert all("未来的章" not in t for t in tails)


def test_best_window_returns_empty_when_no_hit():
    """正文里完全没有查询词时不硬凑窗口。"""
    assert rt._best_window("完全无关的正文内容。" * 20, ["玄铁令"]) == ""


def test_best_window_focuses_on_hit_area():
    window = rt._best_window(
        "无关铺垫。" * 100 + "玄铁令在此。" + "无关收尾。" * 100, ["玄铁令"]
    )
    assert "玄铁令" in window
    assert len(window) <= 400


def test_best_window_does_not_count_or_keyword():
    """窗口计数用的是原始词表:拼串里的 OR 不许被当成查询词去数。"""
    body = "OR" * 200 + "玄铁令"
    window = rt._best_window(body, ["玄铁令"])
    assert "玄铁令" in window


# ---------- 结构化路与合并 ----------

def test_structured_path_finds_facts_without_lexical_overlap():
    """结构化路:场景文本与事实毫无词汇重叠,仍然要捞到(靠出场人物)。"""
    db, project, hero, _ = _fixture()
    _seed_facts(
        db, project.id, hero.id,
        [("林晚此刻重伤未愈,动用灵力会撕裂伤口", 5, None, "critical")],
    )
    # 场景文本里完全不提「伤」
    scene = _scene(
        db, project,
        title="夜谈", summary="林晚与旧友在灯下闲谈", goal="交换消息",
        conflict="各有隐瞒", fact_hints=[],
    )
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    contents = " ".join(f["content"] for f in ctx.facts)
    assert "重伤未愈" in contents, "结构化路没把出场人物的事实带进来"


def test_merge_marks_both_when_hit_by_two_paths():
    """两路都命中的事实标 _src=both(评测据此判断检索层是否真的起作用)。"""
    db, project, hero, _ = _fixture()
    _seed_facts(
        db, project.id, hero.id,
        [("林晚持有玄铁令", 1, None, "critical")],
    )
    scene = _scene(db, project)
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    hit = [f for f in ctx.facts if "玄铁令" in f["content"]]
    assert hit and hit[0]["_src"] == "both"


def test_render_facts_block_marks_importance():
    ctx = rt.SceneContext(
        facts=[
            {"content": "重伤未愈", "importance": "critical", "entity_name": "林晚",
             "valid_from": 5},
            {"content": "戴着戒指", "importance": "major", "entity_name": "林晚",
             "valid_from": 2},
        ]
    )
    block = rt.render_facts_block(ctx)
    assert "❗" in block and "·" in block
    assert "林晚:重伤未愈" in block
    assert "自第5章起" in block


def test_render_facts_block_empty():
    assert "暂无" in rt.render_facts_block(rt.SceneContext())


# ---------- 降级与容错 ----------

def test_retrieval_survives_without_fts():
    """没有 FTS5 虚表(全新库未跑迁移)→ 回落结构化路,不抛异常。"""
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Entity, Project, Scene
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(title="x", topic="y", genre="z",
                      target_chapters=5, target_words_per_chapter=2000)
    db.add(project)
    db.commit()
    ent = Entity(project_id=project.id, entity_type="character", name="林晚")
    db.add(ent)
    db.commit()
    from app.db.models import Fact

    db.add(Fact(project_id=project.id, entity_id=ent.id, fact_type="state",
                content="林晚带着剑", valid_from=1, importance="major"))
    db.commit()
    scene = Scene(project_id=project.id, outline_id=1, chapter_number=2, seq=1,
                  title="夜行", summary="林晚出门", characters=["林晚"])
    db.add(scene)
    db.commit()

    ctx = rt.retrieve_for_scene(db, project.id, scene, 2)
    assert ctx.stats["fts"] is False
    assert any("带着剑" in f["content"] for f in ctx.facts)


def test_retrieval_handles_fts_query_syntax_error():
    """场景文本含 FTS 语法字符(引号/括号)→ 不许把异常抛给生成链路。"""
    db, project, _, _ = _fixture()
    scene = _scene(
        db, project,
        title='他说"别动"(真的)',
        summary="冲突*突然^爆发:有人-喊了-一声",
    )
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    assert isinstance(ctx, rt.SceneContext)  # 不抛即通过


def test_retrieval_empty_scene_returns_context():
    db, project, _, _ = _fixture()
    scene = _scene(db, project, title="", summary="", goal="", conflict="", fact_hints=[])
    ctx = rt.retrieve_for_scene(db, project.id, scene, 6)
    assert isinstance(ctx, rt.SceneContext)
    assert ctx.stats["query_terms"] == 0


def test_render_tail_block_empty_when_no_tails():
    assert rt.render_tail_block(rt.SceneContext()) == ""
