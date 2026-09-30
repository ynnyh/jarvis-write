# tests/test_inject_budget_book.py
# -*- coding: utf-8 -*-
"""inject_budget 实书口径的回归测试。

背景:该脚本的 --project/--chapter 模式交付时从未跑通过(导入的函数名不存在、
返回值缺 project_id 导致 render 走错分支),直到拿真实书库跑基线才暴露。
本测试用内存库把「真实组装路径」钉住:接线再烂,测试当场变红,不用等下一次盘点。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Chapter, Outline, Project, User

import inject_budget  # noqa: E402 — 来自 scripts/,靠上面的 sys.path


def _db_with_book():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(username="预算盘点测试")
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, title="注入预算测试书", target_chapters=10)
    db.add(project)
    db.flush()
    db.add(
        Chapter(
            project_id=project.id,
            chapter_number=1,
            draft_content="",
            final_content="林辰推开殡仪馆的侧门,冷气贴着后颈灌进来。" * 20,
        )
    )
    db.add(
        Outline(
            project_id=project.id,
            chapter_number=2,
            title="第二章·夜查",
            summary="林辰夜访法医实验室旧档",
            characters_involved=["林辰"],
            key_items=["铜钥匙"],
        )
    )
    db.commit()
    return db, project


def test_book_report_walks_real_assembly():
    db, project = _db_with_book()
    try:
        data = inject_budget.book_report(project.id, 2, session=db)
        assert "error" not in data, data
        assert data["project_id"] == project.id and data["chapter_number"] == 2
        # 走的是真实拼装:总长该有模板固定指令 + 至少一块上下文的量级
        assert data["total_chars"] > 1000
        names = [n for n, _ in data["blocks_by_size"]]
        assert "本章蓝图" in names  # 模板结构块被切出来了
        assert "最近章节结尾(直接上文,衔接必须自然)" in names  # 上章正文进了上下文
        assert data["data_internal_blocks"] == []  # 干净样本不该有数据内标记
    finally:
        db.close()


def test_book_report_missing_outline_reports_error():
    db, project = _db_with_book()
    try:
        data = inject_budget.book_report(project.id, 9, session=db)
        assert data.get("error") and "蓝图" in data["error"]
    finally:
        db.close()


def test_book_report_restores_preflight_after_run():
    """打桩必须用完还原:不还原会放坏同进程里真正要跑写前审核的其他用例。"""
    import app.engines.pipeline.chapter as chapter_mod

    db, project = _db_with_book()
    original = chapter_mod.preflight_chapter
    try:
        inject_budget.book_report(project.id, 2, session=db)
        assert chapter_mod.preflight_chapter is original
    finally:
        db.close()
        chapter_mod.preflight_chapter = original
