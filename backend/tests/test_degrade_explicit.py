# tests/test_degrade_explicit.py
# -*- coding: utf-8 -*-
"""显式降级回归:关键 LLM 环节失败时,绝不能静默冒充「干净」。

背景(2026-09-08 综合评估发现):
一致性门禁 / 章后事实抽取 / JSON 解析三处失败一律 `return []` 或 `return {}`,
下游无从区分「查过、没问题」与「这一环节根本没跑成」——一致性门禁因此在模型
超时/429 时自动放行,「不崩」的承诺恰好在最需要它的时刻失效。

新语义:失败 → 返回带 `degraded` 标记的哨兵 → 下游隔离待人工复核 / 章末可见,
绝不冒充干净。本文件锁死这条纪律,防止静默降级复发。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from test_continuity_gate import _make_db, _run_generate, _ScriptedCheck, _SpyExtract


def _failing_adapter_cls():
    class _FailingAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            raise RuntimeError("模拟 LLM 超时/网络失败")

    return _FailingAdapter()


# ---------- 一致性门禁:调用失败必须显式降级 ----------
def test_check_chapter_llm_failure_returns_sentinel():
    """LLM 调用失败 → 降级哨兵,不是空列表(空列表会被当成「没有矛盾」放行)。"""
    from app.engines.common import SCOPE_CONSISTENCY, degraded_issue, is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    with patch.object(
        checker_mod, "get_adapter_for", return_value=_failing_adapter_cls()
    ):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert is_degraded(issues), f"LLM 失败应带降级哨兵,实际={issues}"
    assert len(issues) == 1
    assert issues[0]["scope"] == SCOPE_CONSISTENCY
    assert "LLM 调用失败" in issues[0]["reason"]
    # 哨兵必须与真 issue 形状一致,才能落库/回显
    assert issues[0]["description"] and issues[0]["type"] == "degraded"
    # 自检:形状与构造器一致(防止将来手改哨兵结构导致下游识别失败)
    assert issues[0]["severity"] == degraded_issue(SCOPE_CONSISTENCY, "x")["severity"]


def test_check_chapter_bad_json_returns_sentinel():
    """模型说了话但 JSON 坏了 → 同样降级(不是「没有问题」)。"""
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    class _BadJsonAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return "抱歉,我无法按要求输出 JSON:{\"issues\": [截断在半路"

    db, project, _ch1 = _make_db()
    with patch.object(checker_mod, "get_adapter_for", return_value=_BadJsonAdapter()):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert is_degraded(issues)
    assert "解析失败" in issues[0]["reason"]


def test_check_chapter_empty_reply_returns_sentinel():
    """模型返回空内容 → 降级(空回复不是「检查通过」)。"""
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    class _EmptyAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return ""

    db, project, _ch1 = _make_db()
    with patch.object(checker_mod, "get_adapter_for", return_value=_EmptyAdapter()):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert is_degraded(issues)


def test_checker_still_returns_empty_when_nothing_to_compare():
    """回归护栏:第一章且圣经为空(确实无可对照源)仍返回空——这是正常的「不查」,
    不是降级。别把两种「空」混为一谈。"""
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db(with_ch1=False)
    with patch.object(checker_mod, "get_adapter_for", return_value=_failing_adapter_cls()):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 1, "第一章正文。" * 30))

    assert issues == []
    assert not is_degraded(issues)


# ---------- 降级哨兵的下游语义 ----------
def test_continuity_score_degraded_is_not_clean():
    """降级时连续性必须记「未校验」,绝不能回落成 9(干净)——那是纸面门禁的来源。"""
    from app.engines.common import CONTINUITY_UNVERIFIED, SCOPE_CONSISTENCY, degraded_issue
    from app.engines.consistency.checker import continuity_score

    sentinel = degraded_issue(SCOPE_CONSISTENCY, "LLM 调用失败:超时")
    assert continuity_score([sentinel]) == CONTINUITY_UNVERIFIED
    assert continuity_score([sentinel]) != 9
    # 有真实问题时照常算分,哨兵不参与
    real = {
        "severity": "blocker", "type": "state", "description": "矛盾",
        "evidence": "", "conflicting_fact": "", "suggestion": "", "fix_mode": "patch",
    }
    assert continuity_score([sentinel, real]) == 4


def test_blockers_of_ignores_sentinel():
    """降级哨兵不是 blocker:模型一抽风不该把整章一票否决卡死。"""
    from app.engines.common import SCOPE_CONSISTENCY, degraded_issue
    from app.engines.consistency.checker import blockers_of

    sentinel = degraded_issue(SCOPE_CONSISTENCY, "超时")
    assert blockers_of([sentinel]) == []


# ---------- 章后事实抽取:失败必须显式降级 ----------
def test_extract_llm_failure_returns_degraded_stats():
    from app.engines.common import SCOPE_FACT_EXTRACT, stats_degraded
    from app.engines.consistency import extractor as extractor_mod

    db, project, _ch1 = _make_db()
    with patch.object(
        extractor_mod, "get_adapter_for", return_value=_failing_adapter_cls()
    ):
        stats = asyncio.run(extractor_mod.extract_and_apply(db, project.id, 2, "第二章正文。" * 30))

    assert stats_degraded(stats), f"抽取失败应显式降级,实际={stats}"
    assert stats["scope"] == SCOPE_FACT_EXTRACT
    assert "LLM 调用失败" in stats["reason"]
    assert stats_degraded({}) is False  # 空 dict 不算降级标记(向后兼容旧调用方)


def test_extract_bad_json_returns_degraded_stats():
    from app.engines.common import stats_degraded
    from app.engines.consistency import extractor as extractor_mod

    class _BadJsonAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return "这是一些说明文字,没有 JSON。"

    db, project, _ch1 = _make_db()
    with patch.object(extractor_mod, "get_adapter_for", return_value=_BadJsonAdapter()):
        stats = asyncio.run(extractor_mod.extract_and_apply(db, project.id, 2, "第二章正文。" * 30))

    assert stats_degraded(stats)


def test_parse_llm_json_checked_reports_reason():
    """parse_llm_json_checked 把失败原因交出来;成功时原因为 None。"""
    from app.engines.consistency.extractor import parse_llm_json, parse_llm_json_checked

    data, err = parse_llm_json_checked('{"issues": []}')
    assert err is None and data == {"issues": []}

    data, err = parse_llm_json_checked("半截输出 {")
    assert data == {} and err and "解析失败" in err

    data, err = parse_llm_json_checked("")
    assert data == {} and err

    # 兼容入口行为不变(仍返回 dict),但关键链路应改用 checked 版本
    assert parse_llm_json("坏了 {") == {}


# ---------- 端到端:门禁降级 → 隔离 + 不进圣经 ----------
def test_gate_degraded_quarantines_and_skips_extraction():
    """门禁没跑成时:章节隔离、不跑章后抽取(未校验的正文不污染圣经)、给用户明话。

    这是评估里「最危险的一处」的回归锁:过去 LLM 一失败,门禁静默放行,
    章节状态 pending_review 且照常抽取——带硬矛盾的正文可能被写进真相库。
    """
    from app.engines.common import SCOPE_CONSISTENCY, degraded_issue

    db, project, _ch1 = _make_db()
    check = _ScriptedCheck([
        [degraded_issue(SCOPE_CONSISTENCY, "LLM 调用失败:模拟超时")]
    ])
    extract = _SpyExtract()
    _adapter, (chapter, issues, stats, _guard, review, _pf) = _run_generate(
        db, project, 2, check, extract
    )

    assert chapter.status == "quarantined", (
        "门禁降级必须隔离,不能 pending_review 放行"
    )
    assert extract.calls == 0, "未校验的正文不得走章后抽取(会污染故事圣经)"
    assert stats == {}
    assert review["passed"] is False
    assert "未" in (review.get("gate_note") or ""), "必须给用户一句人话说明未校验"
    # 哨兵落库:用户在问题面板能看见「未校验」,而不是章节静悄悄显示已完成
    from app.db.models import ChapterIssue

    stored = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == chapter.id, ChapterIssue.source == "gate"
    ).all()
    assert any("未校验" in (i.description or "") for i in stored), (
        f"降级哨兵应落进 chapter_issues,实际={[i.description for i in stored]}"
    )


# ---------- 主审:没审成 ≠ 写得差 ----------
def test_review_chapter_parse_failure_marks_degraded():
    """主审输出解析失败 → 显式 degraded。过去会静默变四维 0 分,被当成「写得差」。"""
    from app.engines import editorial as ed_mod

    class _BadJsonAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return "主审意见:本章写得还不错,但{"

    with patch.object(ed_mod, "get_adapter_for", return_value=_BadJsonAdapter()):
        result = asyncio.run(ed_mod.review_chapter("正文。" * 50, "大纲块"))

    assert result["degraded"] is True
    assert "解析失败" in result["degraded_reason"]
    assert result["scores"]["plot"] == 0
    # 自审标记必须是可判定的 bool(读配置失败时保守 False,不许抛)
    assert isinstance(result["self_review"], bool)


def test_review_is_self_reviewing_returns_bool():
    """自审检测:无论配置怎么变,都只返回 bool 且不抛(它是提示,不能拖垮生成)。"""
    from app.llm.router import review_is_self_reviewing

    assert isinstance(review_is_self_reviewing(), bool)


def test_review_degraded_quarantines_without_rework():
    """主审没审成 → 隔离待人工,绝不因「四维 0 分」回炉重写(重写解决不了解析问题)。"""
    async def _degraded_review(content, outline_block):
        return {
            "scores": {"plot": 0, "prose": 0, "pacing": 0, "character": 0},
            "score_reasons": {},
            "comment": "",
            "suggestions": [],
            "degraded": True,
            "degraded_reason": "JSON 解析失败(Unterminated string)",
            "self_review": False,
        }

    db, project, _ch1 = _make_db(review_max_revisions=3)
    check = _ScriptedCheck([[]])  # 门禁干净,问题出在主审
    extract = _SpyExtract()
    _a, (chapter, _issues, _stats, _g, review, _pf) = _run_generate(
        db, project, 2, check, extract, review_fn=_degraded_review
    )

    assert chapter.status == "quarantined", "主审没审成必须隔离,不能当成写得差放行"
    assert extract.calls == 0, "未审成不得写圣经"
    assert review.get("degraded") is True
    assert "主审" in (review.get("review_note") or ""), "必须给用户一句人话说明"
    # 关键:没有为「四维 0 分」白烧回炉轮次
    assert review.get("revision_rounds") == 0
