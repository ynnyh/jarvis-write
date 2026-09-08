# tests/test_bible_resolve.py
# -*- coding: utf-8 -*-
"""圣经实体批量解析:query_facts_at 不再逐名查库(N+1,P2-14)。

验证:
- 名字/别名/未知名混合解析结果正确
- N 个名字只花 1 次 Entity 查询(SQLAlchemy event 计数)
- 过滤行为与旧实现等价:未知名不误伤其他实体的结果
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Entity, Fact, Project
from app.engines.consistency.bible import BibleService


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(title="N+1测试书", target_chapters=5)
    db.add(project)
    db.flush()
    # 3 个实体:主角(带别名)、配角、路人
    hero = Entity(project_id=project.id, entity_type="character",
                  name="林辰", aliases=["小辰", "辰哥"], base_profile={})
    side = Entity(project_id=project.id, entity_type="character",
                  name="苏晓", aliases=[], base_profile={})
    db.add_all([hero, side])
    db.flush()
    db.add_all([
        Fact(project_id=project.id, entity_id=hero.id, fact_type="state",
             content="剑术通神", valid_from=1, importance="major", source_chapter=1),
        Fact(project_id=project.id, entity_id=side.id, fact_type="state",
             content="身中寒毒", valid_from=2, importance="critical", source_chapter=2),
    ])
    db.commit()
    return db, project


def test_resolve_entities_mixed_names():
    db, project = _db()
    bible = BibleService(db, project.id)
    resolved = bible.resolve_entities(["林辰", "辰哥", "苏晓", "不存在的人", "  ", ""])
    assert set(resolved) == {"林辰", "辰哥", "苏晓"}
    assert resolved["林辰"].name == "林辰"
    assert resolved["辰哥"].name == "林辰"  # 别名命中同一实体
    db.close()


def test_query_facts_at_one_entity_query_for_n_names():
    """10 个名字只花 1 次 Entity 查询(旧实现 10~20 次)。"""
    db, project = _db()
    bible = BibleService(db, project.id)
    names = ["林辰", "辰哥", "苏晓"] + [f"配角{i}" for i in range(8)]  # 11 个名字

    queries: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(db.bind, "before_cursor_execute", _count)
    try:
        facts = bible.query_facts_at(5, names)
    finally:
        event.remove(db.bind, "before_cursor_execute", _count)

    # 结果正确:两个实体的有效事实都命中(未知名不误伤)
    assert {f.content for f in facts} == {"剑术通神", "身中寒毒"}
    # Entity 表的查询只有 1 条(其余为 Fact 查询与项目查询)
    entity_queries = [s for s in queries if "entities" in s]
    assert len(entity_queries) == 1, f"Entity 查询 {len(entity_queries)} 次"
    db.close()


def test_query_facts_at_all_unknown_returns_empty():
    db, project = _db()
    bible = BibleService(db, project.id)
    assert bible.query_facts_at(5, ["甲", "乙"]) == []
    db.close()
