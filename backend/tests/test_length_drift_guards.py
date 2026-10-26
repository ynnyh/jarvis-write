# tests/test_length_drift_guards.py
# -*- coding: utf-8 -*-
"""篇幅漂移防线回归(2026-10 修复,mock LLM,无需 API key)。

背景:滚动摘要/文风备忘是「上一版输出 → 下一版输入」的自我反馈字段,prompt 里
写死的字数上限管不住模型(实测项目4:摘要 19 章从 539 字棘轮到 2814 字,3.5×),
「上下文越来越长 → 前情越来越细密 → 新章越写越长」互相咬合。本文件锁三道防线:

  1. 代码级封顶(_capped_field):容忍带内零额外调用;超带重压一次;仍超句边界截断;
  2. 单一入口(summarize_rolling / update_style_memo)全部走封顶,模板 {cap} 与常量同源;
  3. 新项目字数守卫默认开启(模型并不自律,无守卫时章均可漂到 2×+ 目标)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from app.db.models import Project
from app.engines.pipeline import chapter_maintenance as cm
from app.engines.pipeline.scene_write import _word_target_line
from app.prompts.chapter import (
    MEMO_COMPRESS_PROMPT,
    ROLLING_SUMMARY_PROMPT,
    STYLE_MEMO_UPDATE_PROMPT,
    SUMMARY_COMPRESS_PROMPT,
)


# ---------- _truncate_at_sentence:句边界截断 ----------

def test_truncate_at_sentence_cuts_on_boundary():
    text = "第一件事。" + "细节," * 300 + "最后一件事。"
    cut = cm._truncate_at_sentence(text, 100)
    assert len(cut) <= 100
    assert cut.endswith(("。", ",", "，", "\n")) or len(cut) == 100


def test_truncate_at_sentence_noop_within_limit():
    assert cm._truncate_at_sentence("短文本。", 100) == "短文本。"


# ---------- _capped_field:自我反馈字段封顶 ----------

def _adapter(outputs: list[str]) -> Mock:
    """按序吐出 outputs 的假 adapter(AsyncMock,可断言调用次数)。"""
    adapter = Mock()
    adapter.ask = AsyncMock(side_effect=list(outputs))
    return adapter


def test_capped_field_within_band_no_extra_call():
    """容忍带内(≤cap×1.5)信任指令,不触发重压——稳态零额外调用。"""
    adapter = _adapter(["不该被调用"])
    text = "摘" * 1000  # cap=800 → 带 1200,1000 在带内
    out = asyncio.run(cm._capped_field(
        adapter, text, cap=800, compress_prompt=SUMMARY_COMPRESS_PROMPT
    ))
    assert out == text
    adapter.ask.assert_not_called()


def test_capped_field_over_band_recompresses_once():
    """超带 → 重压一次,更短的结果被采纳。"""
    long_text = "摘" * 2000
    adapter = _adapter(["压" * 500])  # _capped_field 只发压缩这一次调用
    out = asyncio.run(cm._capped_field(
        adapter, long_text, cap=800, compress_prompt=SUMMARY_COMPRESS_PROMPT
    ))
    assert out == "压" * 500
    assert adapter.ask.call_count == 1


def test_capped_field_recompress_fails_truncates():
    """重压仍超(或失败)→ 句边界截断,长度绝不超过硬上限。"""
    long_text = "句子一。" * 400  # 2000 字,无句末标点以外的东西
    adapter = _adapter([long_text, "还是长" * 700])  # 重压结果反而更长 → 不采纳
    out = asyncio.run(cm._capped_field(
        adapter, long_text, cap=800, compress_prompt=SUMMARY_COMPRESS_PROMPT
    ))
    assert len(out) <= int(800 * cm._CAP_TOLERANCE)


def test_capped_field_llm_error_truncates_original():
    """重压调用抛异常不外传:截断原文本兜底,摘要更新绝不搞挂章后链路。"""
    adapter = Mock()

    async def boom(prompt: str, system: str | None = None) -> str:
        raise RuntimeError("渠道全灭")

    adapter.ask = boom
    text = "句。" * 500
    out = asyncio.run(cm._capped_field(
        adapter, text, cap=800, compress_prompt=SUMMARY_COMPRESS_PROMPT
    ))
    assert len(out) <= int(800 * cm._CAP_TOLERANCE)


# ---------- summarize_rolling:单一入口带封顶 ----------

def test_summarize_rolling_caps_output():
    overlong = "前情。" * 600  # 3000 字,实测棘轮形态
    adapter = _adapter([overlong, "压好的摘要。"])
    with patch.object(cm, "get_adapter_for", return_value=adapter):
        out = asyncio.run(cm.summarize_rolling("旧", 3, "标题", "正文"))
    assert out == "压好的摘要。"
    # 第一次:生成;第二次:重压
    assert adapter.ask.call_count == 2


def test_summarize_rolling_passes_cap_to_prompt():
    """模板 {cap} 与代码常量同源——format 缺 key 会当场炸,契约由本测试钉住。"""
    prompts: list[str] = []
    adapter = Mock()

    async def ask(prompt: str, system: str | None = None) -> str:
        prompts.append(prompt)
        return "短摘要。"

    adapter.ask = ask
    with patch.object(cm, "get_adapter_for", return_value=adapter):
        asyncio.run(cm.summarize_rolling("旧", 1, "标题", "正文"))
    assert str(cm.ROLLING_SUMMARY_CAP) in prompts[0]


def test_update_style_memo_stores_capped_result():
    """备忘超带 → 落库前被压回;带内原样保存。"""
    from app.db.base import Base
    import app.db.models  # noqa: F401 — 注册全部表
    from app.db.session import SessionLocal, engine

    Base.metadata.create_all(engine)
    db = SessionLocal()
    proj = Project(title="cap-test")
    db.add(proj)
    db.commit()

    adapter = Mock()

    # 按 prompt 内容分流(生成 vs 重压),不依赖调用序
    async def ask(prompt: str, system: str | None = None) -> str:
        return "压好的备忘。" if "压缩到" in prompt else "备" * 1500

    adapter.ask = ask
    try:
        with patch.object(cm, "get_adapter_for", return_value=adapter):
            memo = asyncio.run(cm.update_style_memo(db, proj, 2, "正文" * 100))
        assert memo == "压好的备忘。"
        assert proj.style_memo == "压好的备忘。"
        assert len(proj.style_memo) <= int(cm.STYLE_MEMO_CAP * cm._CAP_TOLERANCE)
    finally:
        db.query(Project).filter(Project.id == proj.id).delete()
        db.commit()
        db.close()


# ---------- 模板契约:封顶值参数化,format 必须全 key 齐 ----------

def test_prompt_templates_format_with_code_constants():
    ROLLING_SUMMARY_PROMPT.format(
        previous_summary="旧", chapter_number=1, chapter_title="t",
        chapter_text="正文", cap=cm.ROLLING_SUMMARY_CAP,
    )
    STYLE_MEMO_UPDATE_PROMPT.format(
        previous_memo="旧", chapter_number=1, chapter_text="正文",
        flavor_notes="", cap=cm.STYLE_MEMO_CAP,
    )
    SUMMARY_COMPRESS_PROMPT.format(actual_len=999, cap=800, text="x")
    MEMO_COMPRESS_PROMPT.format(actual_len=999, cap=600, text="x")


def test_style_memo_prompt_bans_length_drift():
    """篇幅纪律护栏:备忘不得记录/强化「越写越长」(正反馈环的合法化入口)。"""
    assert "篇幅纪律不属于文风" in STYLE_MEMO_UPDATE_PROMPT
    assert "越写越长" in STYLE_MEMO_UPDATE_PROMPT


def test_rolling_summary_prompt_demands_shrink_when_overlong():
    """超长输入必须先瘦身再融入——棘轮的 prompt 侧对抗。"""
    assert "瘦身" in ROLLING_SUMMARY_PROMPT


# ---------- 场景字数行:下限随目标等比 ----------

def test_word_target_line_floor_scales_with_target():
    """旧 max(600,·) 硬底会在短目标场景把下限抬过目标本身,现在等比。"""
    scene = Mock()
    scene.target_words = 666
    line = _word_target_line(2000, scene)
    assert "666" in line
    assert "444" in line  # 下限 = 666*2//3(等比,不再被 600 硬底抬升)
    assert "832" in line  # 上限 = 666*5//4


# ---------- 新项目字数守卫默认开启 ----------

def test_word_guard_default_on_for_new_projects():
    """ORM 列默认 True(创建路径不显式传该字段,默认即生效)。"""
    col = Project.__table__.c.word_guard_enabled
    assert col.default is not None and col.default.arg is True
