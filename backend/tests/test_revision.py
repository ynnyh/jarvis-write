# tests/test_revision.py
# -*- coding: utf-8 -*-
"""重写意见(revision)注入测试(mock LLM,无需 API key)。

验证点:
- 章节已有正文 + 传 revision → 草稿 prompt 含意见文本与上一版截断内容
- 上一版超长 → 截断为前 1500 字 + "……(后略)",不完整注入
- 首次生成(无正文)即使传 revision 也不注入重写块
- 已有正文但 revision 为空 → 不注入重写块
- 失配章(is_stale,大纲已更新)→ 注入「按新大纲重写」指令,不再注入旧正文节选;
  意见可留空也注入;有补充意见则一并带上
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from tests.test_pipeline import MockAdapter

# 2000 字旧正文,超出 _REVISION_EXCERPT_CHARS(1500),用于验证截断
PREVIOUS_TEXT = "旧版正文,节奏拖沓。" * 200


def _make_db(with_previous: bool, stale: bool = False):
    """独立内存库:一个项目 + 第 1 章大纲,可选已生成的上一版正文(可选失配标记)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="重写测试书", target_chapters=1, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    outline = Outline(
        project_id=project.id, chapter_number=1, title="雨夜",
        summary="主角登场", current_version=1,
    )
    db.add(outline)
    db.flush()
    if with_previous:
        db.add(Chapter(
            project_id=project.id, outline_id=outline.id, chapter_number=1,
            draft_content=PREVIOUS_TEXT, final_content=PREVIOUS_TEXT,
            word_count=len(PREVIOUS_TEXT),
            status="stale" if stale else "approved", is_stale=stale,
        ))
    db.commit()
    return db, project


async def _fake_check(*args, **kwargs):
    return []


async def _fake_extract(*args, **kwargs):
    return {}


async def _fake_proofread(*args, **kwargs):
    # 审校把关里的校对:无硬伤,不触发精确替换
    return {"issues": []}


async def _fake_review(*args, **kwargs):
    # 审校把关里的主审:四维满分 → 直接达标,不触发回炉(本测试只关心 revision 注入)
    return {
        "scores": {"plot": 9, "prose": 9, "pacing": 9, "character": 9},
        "comment": "",
        "suggestions": [],
    }


def _run_generate(db, project, revision: str | None) -> MockAdapter:
    """mock LLM 跑一遍 generate_chapter,返回记录了全部 prompt 的 adapter。"""
    from app.engines.pipeline import chapter as ch_mod

    # 草稿 → 定稿 → 滚动摘要,共 3 次调用(检查/抽取/审校把关已 patch 掉)
    adapter = MockAdapter(["新版草稿", "新版定稿", "新滚动摘要"])
    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review),
    ):
        asyncio.run(ch_mod.generate_chapter(db, project, 1, revision=revision))
    return adapter


def test_rewrite_injects_revision_and_previous_excerpt():
    db, project = _make_db(with_previous=True)
    adapter = _run_generate(db, project, "节奏太拖,加强冲突")

    draft_prompt = adapter.calls[0]
    assert "【重写要求】" in draft_prompt
    assert "节奏太拖,加强冲突" in draft_prompt
    # 上一版正文作反面参照,截断注入:前 1500 字在,完整 2000 字不在
    assert "【上一版正文" in draft_prompt
    assert PREVIOUS_TEXT[:1500] in draft_prompt
    assert "……(后略)" in draft_prompt
    assert PREVIOUS_TEXT not in draft_prompt


def test_first_generation_ignores_revision():
    db, project = _make_db(with_previous=False)
    adapter = _run_generate(db, project, "节奏太拖")

    draft_prompt = adapter.calls[0]
    assert "【重写要求】" not in draft_prompt
    assert "【上一版正文" not in draft_prompt
    assert "节奏太拖" not in draft_prompt


def test_rewrite_without_revision_injects_nothing():
    db, project = _make_db(with_previous=True)
    adapter = _run_generate(db, project, "")

    draft_prompt = adapter.calls[0]
    assert "【重写要求】" not in draft_prompt
    assert "【上一版正文" not in draft_prompt


def test_stale_rewrite_uses_new_outline_no_old_excerpt():
    """失配章(大纲已更新)重写:注入「按新大纲重写」指令,旧正文节选不再注入。"""
    db, project = _make_db(with_previous=True, stale=True)
    adapter = _run_generate(db, project, "")

    draft_prompt = adapter.calls[0]
    assert "【按新大纲重写】" in draft_prompt
    assert "不要延续" in draft_prompt
    # 旧正文不再作反面参照注入(避免把模型锚回旧情节)
    assert "【上一版正文" not in draft_prompt
    assert PREVIOUS_TEXT[:1500] not in draft_prompt


def test_stale_rewrite_with_revision_carries_supplement():
    """失配章 + 用户补充意见:指令块带上意见,但仍不注入旧正文。"""
    db, project = _make_db(with_previous=True, stale=True)
    adapter = _run_generate(db, project, "节奏再快一点")

    draft_prompt = adapter.calls[0]
    assert "【按新大纲重写】" in draft_prompt
    assert "节奏再快一点" in draft_prompt
    assert "【上一版正文" not in draft_prompt


def test_non_stale_rewrite_keeps_old_behavior():
    """未失配的章重写:仍是旧的"意见 + 旧文反面参照"模式(回归保护)。"""
    db, project = _make_db(with_previous=True, stale=False)
    adapter = _run_generate(db, project, "节奏太拖")

    draft_prompt = adapter.calls[0]
    assert "【按新大纲重写】" not in draft_prompt
    assert "【重写要求】" in draft_prompt
    assert "【上一版正文" in draft_prompt
