# tests/test_fk_enforcement.py
# -*- coding: utf-8 -*-
"""外键约束开启(P1-6)后的行为验证。

PRAGMA foreign_keys=ON 打通模型里声明的 ondelete:
- 脏引用(指向不存在的外键)在写入时被拒;
- ON DELETE CASCADE / SET NULL 随父行删除生效;
- 手工级联(deps.delete_project_cascade)与约束共存不冲突。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Chapter, Entity, Fact, Outline, Project


def _db():
    """普通引擎(经 session.py 的同一套 pragma):验证约束真的开着。"""
    from app.db.session import engine  # noqa: F401 — 触发 connect 事件注册

    # 独立内存库但手动执行同一套 pragma,避免测试引擎与生产引擎行为分叉
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    return db, engine


def test_fk_pragma_is_active():
    db, engine = _db()
    raw = engine.raw_connection()
    cur = raw.cursor()
    cur.execute("PRAGMA foreign_keys")
    assert cur.fetchone()[0] == 1
    raw.close()
    db.close()


def test_dirty_reference_rejected_at_write():
    """插入指向不存在项目/实体的行 → IntegrityError(以前静默成功成孤儿)。"""
    db, engine = _db()
    with pytest.raises(IntegrityError):
        db.add(Chapter(project_id=9999, chapter_number=1))
        db.flush()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.add(Fact(project_id=9999, entity_id=4242, fact_type="state",
                    content="x", valid_from=1))
        db.flush()
    db.close()


def test_ondelete_semantics_enforced():
    """删项目 → 章/事实级联消失;删大纲 → 章 outline_id 置空(SET NULL)。"""
    db, _ = _db()
    p = Project(title="级联书", target_chapters=3)
    db.add(p)
    db.flush()
    o = Outline(project_id=p.id, chapter_number=1, title="第一章",
                current_version=1)
    db.add(o)
    db.flush()
    ch = Chapter(project_id=p.id, outline_id=o.id, chapter_number=1)
    db.add(ch)
    db.flush()
    ent = Entity(project_id=p.id, entity_type="character", name="林辰")
    db.add(ent)
    db.flush()
    db.add(Fact(project_id=p.id, entity_id=ent.id, fact_type="state",
                content="剑术通神", valid_from=1))
    db.commit()

    # 删大纲:章节保留,outline_id 被 SET NULL
    db.delete(o)
    db.commit()
    db.refresh(ch)
    assert ch.outline_id is None

    # 删项目:章/实体/事实全部级联删除
    db.delete(p)
    db.commit()
    assert db.query(Chapter).count() == 0
    assert db.query(Entity).count() == 0
    assert db.query(Fact).count() == 0
    db.close()
