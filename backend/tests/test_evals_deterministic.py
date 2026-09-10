# tests/test_evals_deterministic.py
# -*- coding: utf-8 -*-
"""确定性轨(docs/15 §7.1 轨 A):不调 LLM 也能做质量回归。

验证:
- 指标口径与质量轨**同源**(复用 thresholds 的门槛定义,不另造尺子)
- 空样本 = 失败(不是「通过」——没样本说明夹具/语料缺失)
- 只判本轨负责的指标;质量轨独有的项缺席不算违规(否则 CI 恒红)
- 越界确实拦下
"""
from __future__ import annotations

import pytest

from app.evals.deterministic import (
    DETERMINISTIC_OWNED,
    evaluate_texts,
    format_summary,
    format_violations,
    score_texts,
)
from app.evals.mutation import BASELINE_TEXT


def test_score_texts_aggregates_basic_shape():
    run = score_texts([BASELINE_TEXT, BASELINE_TEXT], target_words=200)
    assert run.chapters == 2
    a = run.aggregate
    assert a["chapters_total"] == 2
    assert a["mean_flavor"] is not None
    assert a["total_chars"] > 0
    assert len(run.per_chapter) == 2


def test_empty_samples_fail_not_pass():
    """没正文样本必须判失败,不能默认通过。"""
    run, ok, violations = evaluate_texts([])
    assert run.chapters == 0
    assert ok is False
    assert violations
    assert "无法评估" in violations[0]["detail"]


def test_blank_only_samples_treated_as_empty():
    run, ok, _ = evaluate_texts(["", "   \n  "])
    assert run.chapters == 0
    assert ok is False


def test_quality_track_only_metrics_absent_do_not_trigger_violation():
    """质量轨独有的指标(主审达标率/blocker/事实数)在确定性轨缺席,不算违规。"""
    _, ok, violations = evaluate_texts([BASELINE_TEXT], target_words=200)
    # 干净样本应通过——若把「pass_rate 缺失」当违规,这里会红
    assert ok is True, violations
    assert not any(v["metric"] == "pass_rate" for v in violations)


def test_repetition_violation_is_caught():
    """复读超门槛必须拦下(这是本轨存在的意义)。

    两条口径细节(读代码比猜重要):
      · `within_repeats_total` 计的是**不同复读句的「种类数」**,不是重复次数
        ——所以要让数值上去,得注入多种句子,而不是同一句刷很多遍;
      · 只认 >= _MIN_SENT_CHARS(6 字)的句子,太短的对白(「嗯。」)不算复读。
    """
    lines = "".join(
        f"这是第{i}种反复出现的无意义填充句子内容。" * 2 for i in range(1, 8)
    )
    _, ok, violations = evaluate_texts(
        [BASELINE_TEXT + "\n" + lines], target_words=200
    )
    assert ok is False
    assert any(v["metric"] == "within_repeats_total" for v in violations)


def test_target_ratio_checked_when_target_given():
    """给了目标字数,篇幅比也进门槛判定。"""
    long_text = BASELINE_TEXT * 10
    _, ok, violations = evaluate_texts([long_text], target_words=100)
    assert ok is False
    assert any(v["metric"] == "mean_target_ratio" for v in violations)


def test_owned_metrics_is_exactly_the_deterministic_set():
    """本轨负责的键必须显式且窄——放宽它等于放弃检查。"""
    assert DETERMINISTIC_OWNED == {
        "mean_flavor", "within_repeats_total", "mean_target_ratio",
    }


def test_format_summary_and_violations_render():
    run = score_texts([BASELINE_TEXT], target_words=200)
    assert "确定性轨" in format_summary(run)
    assert format_violations([]) == "  全部确定性指标在门槛内。"
    assert "✗" in format_violations([
        {"detail": "x 超限", "metric": "x", "rule": "", "value": 1}
    ])


def test_summary_handles_empty_run():
    assert format_summary(score_texts([])) == "确定性轨:无样本"
