# tests/test_deai_p4.py
# -*- coding: utf-8 -*-
"""P4 自愈埋记录 + 权重/门槛热更测试(mock LLM,无需 API key)。

验证点:
- 生成时采纳了去味重写:去味前正文存一版快照(source=deai,前端「放弃去味」
  回退用),分数变化透传 review.deai
- 干净正文:不产生 deai 快照(自愈短路,不调 LLM)
- 权重热更:覆盖后同类文本得分变化,回空即回到出厂值
- 门槛热更:gate 覆盖为 0 时干净文本也触发重写、抬高后不触发
- 管理端配置落库 AppSetting 并同步内存(载入函数回放同一份配置)
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from tests.test_pipeline import MockAdapter

from app.engines.polish.ai_flavor import (
    ai_flavor_report,
    set_gate_override,
    set_weight_overrides,
    weight_overrides,
)

DIRTY_CH1 = (
    "她眼中闪过一丝慌乱,嘴角勾起一抹弧度。他沉默片刻,微微一笑,缓缓开口。"
    "他轻声说道,语气平静。空气仿佛凝固了,时间仿佛静止了。"
    "他不禁叹了口气,下意识地握紧了拳头。"
) * 3
# 干净白描:长度落在去味篇幅安全阀(0.75-1.25)内,且 score 远低于门槛
CLEAN_TEXT = "老张把烟头摁灭在墙上,说走吧。巷子里没人。风把门带上,咣当一声。" * 6


# ---------- 权重 / 门槛热更 ----------


def test_weight_overrides_change_score():
    """把最重的类别权重压到近 0 → 同一脏文本分数显著下降;回空恢复。"""
    base = ai_flavor_report(DIRTY_CH1).score
    try:
        set_weight_overrides({"万能神态套话": 0.1, "稳妥表达癖": 0.1})
        lowered = ai_flavor_report(DIRTY_CH1).score
        assert lowered < base
        assert weight_overrides() == {"万能神态套话": 0.1, "稳妥表达癖": 0.1}
    finally:
        set_weight_overrides({})
    assert weight_overrides() == {}
    assert ai_flavor_report(DIRTY_CH1).score == base


def test_weight_overrides_ignore_unknown_category():
    """类别名不存在的覆盖直接忽略(不炸、不生效)。"""
    try:
        set_weight_overrides({"不存在的类别": 9.9})
        assert weight_overrides() == {}
    finally:
        set_weight_overrides({})


def test_gate_override_changes_trigger():
    """门槛热更:压到 0 连干净文本都触发重写;抬高到 100 脏文本也不触发。"""
    from app.engines.polish.polisher import deai_self_heal

    calls: list[str] = []

    async def spy(text, report, style_block=""):
        calls.append(text[:10])
        return ""  # 空输出 → 丢弃本轮,但调用已发生

    try:
        set_gate_override(-1.0)  # 负门槛:score=0 的干净文本也"超标"
        with patch("app.engines.polish.polisher.deai_rewrite", new=spy):
            asyncio.run(deai_self_heal(CLEAN_TEXT[:60]))
        assert calls, "门槛压到负值时干净文本应触发重写"

        calls.clear()
        set_gate_override(10000.0)  # DIRTY_CH1 是套话堆砌,得分远超 100,门槛要抬到天上
        with patch("app.engines.polish.polisher.deai_rewrite", new=spy):
            asyncio.run(deai_self_heal(DIRTY_CH1))
        assert not calls, "门槛 10000 时脏文本也不该触发"
    finally:
        set_gate_override(None)


def test_admin_flavor_config_roundtrip():
    """管理端配置:落库 AppSetting + 内存生效;载入函数回放同一份配置。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api import admin as admin_mod
    from app.api.admin import _apply_flavor_config, load_ai_flavor_config
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import AppSetting
    from app.engines.polish.polisher import get_deai_gate

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        out = _apply_flavor_config(db, admin_mod.AiFlavorConfigIn(
            gate_score=7.5, weights={"万能神态套话": 1.0},
        ))
        assert out.gate_score == 7.5
        assert out.weights["万能神态套话"] == 1.0
        assert db.get(AppSetting, "ai_flavor_config") is not None

        # 模拟重启:内存清空 → 从库载入恢复(load 内部现 import SessionLocal,patch 源头)
        set_weight_overrides({})
        set_gate_override(None)
        import app.db.session as db_session_mod

        with patch.object(db_session_mod, "SessionLocal", Session):
            load_ai_flavor_config()
        assert get_deai_gate() == 7.5
        assert weight_overrides() == {"万能神态套话": 1.0}
    finally:
        set_weight_overrides({})
        set_gate_override(None)


# ---------- 生成链路自愈埋记录 ----------


def _make_db():
    """独立内存库:项目 + 第 1 章(已生成)+ 第 2 章大纲。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="自愈埋记录测试书", target_chapters=2, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    db.add(Outline(project_id=project.id, chapter_number=1, title="雨夜",
                   summary="主角登场", current_version=1))
    db.add(Outline(project_id=project.id, chapter_number=2, title="对峙",
                   summary="冲突升级", current_version=1))
    db.flush()
    db.add(Chapter(
        project_id=project.id, chapter_number=1,
        draft_content=CLEAN_TEXT, final_content=CLEAN_TEXT,
        word_count=len(CLEAN_TEXT), status="approved",
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
        "comment": "", "suggestions": [],
    }


def _run_generate(final_text: str, heal_output: str | None):
    """跑第 2 章生成:定稿正文按 final_text 给;heal_output 非空时模拟去味重写输出。"""
    db, project = _make_db()
    from app.engines.pipeline import chapter as ch_mod

    replies = ["草稿正文。", final_text]
    if heal_output is not None:
        replies.append(heal_output)  # deai_rewrite 的输出(Task.FINALIZE)
    replies += ["备忘。", "摘要。", "契约。"]
    adapter = MockAdapter(replies)
    # deai_rewrite 走 polisher 模块自己 import 的 get_adapter_for,两边都要 patch
    # 到同一个 MockAdapter,重写输出才能按调用顺序出队。
    import app.engines.polish.polisher as polish_mod

    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(polish_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review),
    ):
        chapter, _issues, _stats, _guard, review, _pf = asyncio.run(
            ch_mod.generate_chapter(db, project, 2)
        )
    return db, chapter, review


def test_generate_chapter_snapshots_pre_deai_text():
    """定稿脏 → 自愈重写采纳:去味前正文留版本快照,分数变化透传 review.deai。"""
    db, chapter, review = _run_generate(DIRTY_CH1, CLEAN_TEXT)

    from app.db.models import ChapterVersion

    assert chapter.final_content == CLEAN_TEXT
    deai_snaps = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter.id, ChapterVersion.source == "deai")
        .all()
    )
    assert len(deai_snaps) == 1
    assert deai_snaps[0].final_content == DIRTY_CH1  # 去味前的原文
    assert deai_snaps[0].word_count == len(DIRTY_CH1)
    assert review["deai"]["before"] > review["deai"]["after"] > 0


def test_generate_chapter_clean_text_no_deai_snapshot():
    """定稿干净 → 自愈短路:无 deai 快照、review 无 deai 键。"""
    db, chapter, review = _run_generate(CLEAN_TEXT, heal_output=None)

    from app.db.models import ChapterVersion

    assert "deai" not in review
    snaps = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter.id, ChapterVersion.source == "deai")
        .all()
    )
    assert snaps == []
