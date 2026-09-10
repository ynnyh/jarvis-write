# tests/test_chapter_drama_task.py
# -*- coding: utf-8 -*-
"""本章戏剧任务与反 AI 腔分级注入测试(纯函数 + mock LLM,无需 API key)。

治「文绉绉、喝白水」的生成端改动:
- _drama_task_block:按蓝图的定位/悬念密度/认知颠覆/情绪基调/戏核,确定性推导
  「这一章要让读者感受到什么」——高潮章要总爆发、铺垫章要压着但压出不安;
  老蓝图没有情绪基调/戏核两列(空串)时回落成让模型自定,行为不劣化。
- _deai_rules_block:反 AI 腔核心版常驻,扩展版只在最近几章确实脏时追加;
  干净书里少摆禁令,把注意力还给内容。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_pipeline import MockAdapter

from app.engines.pipeline.chapter import _deai_rules_block, _drama_task_block
from app.prompts.chapter import _DEAI_CORE, _DEAI_EXTRA


def _outline(**kw) -> SimpleNamespace:
    """蓝图桩:字段与 outlines 表一致(存量蓝图只是两列为空串)。"""
    base = {
        "chapter_role": "", "suspense_level": "", "plot_twist_level": "",
        "emotional_tone": "", "scene_anchor": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ---------- 本章戏剧任务 ----------

def test_climax_chapter_gets_burst_task():
    """高潮章:要求把最强的场面压在中后段,不是平铺。"""
    block = _drama_task_block(_outline(
        chapter_role="高潮", suspense_level="高", plot_twist_level="★★★★☆",
        emotional_tone="紧绷", scene_anchor="他终于认出那道疤",
    ))
    assert "本章是这一段的总爆发" in block
    assert "每 300-500 字" in block           # 悬念密度=高
    assert "推翻读者既有判断" in block         # 认知颠覆=强
    assert "情绪基调(蓝图已定:紧绷)" in block
    assert "他终于认出那道疤" in block
    assert "该精彩的段落放开写" in block       # 情绪落差常驻


def test_setup_chapter_told_to_press_not_to_fade():
    """铺垫章:可以压着写,但必须「压出东西」——这是治白水的关键措辞。"""
    block = _drama_task_block(_outline(chapter_role="铺垫", suspense_level="低"))
    assert "压是为了后面弹得更高" in block
    assert "不是一句空泛的感叹" in block
    assert "总爆发" not in block


def test_legacy_outline_falls_back_to_model_choice():
    """存量蓝图(基调/戏核为空串)→ 回落成「先给本章定一个调子」,不报错。"""
    block = _drama_task_block(_outline(chapter_role="过渡"))
    assert "先给本章定一个调子" in block
    assert "动笔前先定一个让读者记住的瞬间" in block


def test_unknown_role_still_gets_forward_motion():
    """认不出的定位也要给一条保底任务,不能空着。"""
    block = _drama_task_block(_outline())
    assert "本章要往前推一格" in block


# ---------- 反 AI 腔分级 ----------

def test_deai_rules_core_only_when_book_is_clean():
    """干净书:只注入核心版——少摆禁令,注意力留给内容。"""
    block = _deai_rules_block(["他推门进去。屋里没人。桌上放着一只凉透的碗。"])
    assert block == _DEAI_CORE
    assert _DEAI_EXTRA not in block
    assert "禁神态套话" in block


def test_deai_rules_escalate_when_recent_chapters_dirty():
    """最近几章确实脏 → 追加扩展版。"""
    dirty = "她眼中闪过一丝慌乱,嘴角勾起一抹弧度。" * 10
    assert _deai_rules_block([dirty]) == _DEAI_CORE + _DEAI_EXTRA


def test_deai_rules_accumulate_across_chapters():
    """单章没到线、几章累加过了线也要升级。"""
    mild = "他沉默片刻,缓缓开口。" * 3          # 6 处,单独不够
    assert _deai_rules_block([mild]) == _DEAI_CORE
    assert _deai_rules_block([mild, mild]) == _DEAI_CORE + _DEAI_EXTRA


# ---------- 生成链路接线 ----------

def _make_db():
    """独立内存库:干净的第 1 章 + 第 2 章大纲(定过基调与戏核)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="定调测试书", target_chapters=2, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    clean = "他推门进去。屋里没人。桌上放着一只凉透的碗,碗底压着半张纸。"
    db.add(Chapter(
        project_id=project.id, chapter_number=1,
        draft_content=clean, final_content=clean,
        word_count=len(clean), status="approved",
    ))
    db.add(Outline(
        project_id=project.id, chapter_number=2, title="认疤",
        summary="他终于认出了那道疤", chapter_role="高潮",
        suspense_level="高", emotional_tone="紧绷",
        scene_anchor="他终于认出那道疤", current_version=1,
    ))
    db.commit()
    return db, project


async def _fake_check(*a, **kw):
    return []          # 门禁结果:问题列表


async def _fake_extract(*a, **kw):
    return {}


async def _fake_proofread(*a, **kw):
    return {"issues": []}


async def _fake_review(*a, **kw):
    return {"scores": {"plot": 9, "prose": 9, "pacing": 9, "character": 9},
            "comment": "", "suggestions": []}


def test_generate_chapter_injects_drama_task_into_prompts():
    """草稿与定稿 prompt 都要带本章戏剧任务——定稿不得把它磨平。"""
    db, project = _make_db()
    from app.engines.pipeline import chapter as ch_mod
    from app.engines.pipeline import chapter_compose as cc_mod
    from app.engines.pipeline import chapter_finalize as cf_mod
    from app.engines.pipeline import chapter_maintenance as cm_mod
    from app.engines.pipeline import chapter_rework as rw_mod

    adapter = MockAdapter(["草稿正文。", "定稿正文。", "备忘。", "摘要。", "契约。"])
    with (
        patch.object(cc_mod, "get_adapter_for", return_value=adapter),
        patch.object(cm_mod, "get_adapter_for", return_value=adapter),
        patch.object(rw_mod, "_check", new=_fake_check),
        patch.object(cm_mod, "extract_and_apply", new=_fake_extract),
        patch.object(rw_mod, "_proofread", new=_fake_proofread),
        patch.object(rw_mod, "_review", new=_fake_review),
    ):
        asyncio.run(ch_mod.generate_chapter(db, project, 2))

    draft_prompt = adapter.calls[0]
    assert "本章戏剧任务" in draft_prompt
    assert "他终于认出那道疤" in draft_prompt      # 蓝图定的戏核进了草稿
    assert "本章是这一段的总爆发" in draft_prompt   # 定位=高潮
    # 干净书 → 只给核心版禁令,不给扩展版(注意力留给内容)
    assert "禁 hedge 词" not in draft_prompt
    finalize_prompt = adapter.calls[1]
    assert "本章戏剧任务" in finalize_prompt       # 定稿同样守住调子
