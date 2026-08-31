# tests/test_deai_fatigue.py
# -*- coding: utf-8 -*-
"""生成端疲劳词表(P5 治本项)注入与病灶回流测试(mock LLM,无需 API key)。

验证点:
- fatigue_block:静态高危黑名单常驻;最近几章有病灶时附本书特有 top 类;
  干净文本只有静态黑名单
- generate_chapter:草稿 prompt(经 style_directives)带上疲劳词表
- memo_notes_block:脏报告渲染体检块(供备忘「要避开的」沉淀),干净报告空串
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from tests.test_pipeline import MockAdapter

from app.engines.polish.ai_flavor import ai_flavor_report
from app.engines.polish.polisher import fatigue_block, memo_notes_block

DIRTY_CH1 = (
    "她眼中闪过一丝慌乱,嘴角勾起一抹弧度。他沉默片刻,微微一笑,缓缓开口。"
    "他轻声说道,语气平静。空气仿佛凝固了,时间仿佛静止了。"
    "他不禁叹了口气,下意识地握紧了拳头。"
) * 3


def test_fatigue_block_static_and_dynamic():
    """有病灶的近章 → 静态黑名单 + 本书特有高频类都渲染。"""
    block = fatigue_block([DIRTY_CH1])
    assert "本章禁写的高危句式" in block          # 静态黑名单常驻
    assert "本书最近几章反复出现的 AI 腔" in block  # 动态病灶
    assert "万能神态套话" in block                  # DIRTY 的 top 类点名


def test_fatigue_block_clean_text_only_static():
    """全书干净 → 只有静态黑名单,不编造病灶。"""
    clean = "老张把烟头摁灭在墙上,说走吧。巷子里没人。风把门带上,咣当一声。" * 3
    block = fatigue_block([clean])
    assert "本章禁写的高危句式" in block
    assert "本书最近几章反复出现的" not in block


def test_memo_notes_block_dirty_and_clean():
    dirty = ai_flavor_report(DIRTY_CH1)
    notes = memo_notes_block(dirty)
    assert "AI 味体检" in notes and "万能神态套话" in notes

    clean = ai_flavor_report("老张把烟头摁灭在墙上,说走吧。")
    assert memo_notes_block(clean) == ""


def _make_db():
    """独立内存库:项目 + 第 1 章(已生成,AI 腔正文)+ 第 2 章大纲。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="疲劳词表测试书", target_chapters=2, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="雨夜",
        summary="主角登场", current_version=1,
    ))
    db.add(Outline(
        project_id=project.id, chapter_number=2, title="对峙",
        summary="冲突升级", current_version=1,
    ))
    db.flush()
    db.add(Chapter(
        project_id=project.id, chapter_number=1,
        draft_content=DIRTY_CH1, final_content=DIRTY_CH1,
        word_count=len(DIRTY_CH1), status="approved",
    ))
    db.commit()
    return db, project


async def _fake_check(*args, **kwargs):
    return []


async def _fake_extract(*args, **kwargs):
    return {}


async def _fake_proofread(*args, **kwargs):
    return {"issues": []}


async def _fake_review(*args, **kwargs):
    return {
        "scores": {"plot": 9, "prose": 9, "pacing": 9, "character": 9},
        "comment": "",
        "suggestions": [],
    }


def test_generate_chapter_injects_fatigue_into_draft_prompt():
    """第 2 章生成:上一章病灶 → 草稿/定稿 prompt 带疲劳词表(生成端设防)。"""
    db, project = _make_db()
    from app.engines.pipeline import chapter as ch_mod

    # 草稿/定稿正文给干净的 → 自愈短路,不多耗 LLM 调用;末尾备忘更新要一次
    adapter = MockAdapter(["草稿正文。", "定稿正文。", "备忘。", "摘要。", "契约。"])
    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review),
    ):
        asyncio.run(ch_mod.generate_chapter(db, project, 2))

    draft_prompt = adapter.calls[0]
    assert "本章禁写的高危句式" in draft_prompt       # 静态黑名单进草稿
    assert "本书最近几章反复出现的 AI 腔" in draft_prompt  # 第 1 章病灶点名
    assert "万能神态套话" in draft_prompt
    finalize_prompt = adapter.calls[1]
    assert "本章禁写的高危句式" in finalize_prompt    # 定稿同样吃 style_block
