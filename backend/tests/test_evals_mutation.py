# tests/test_evals_mutation.py
# -*- coding: utf-8 -*-
"""判别力轨(docs/15 §7.1 轨 C):验证门槛**确实拦得住**退化。

这是对「评测体系本身」的检验:如果故意写坏的正文都测不出问题,那门槛数值
再严也只是摆设。本文件把这些断言钉死——哪天有人把 ai_flavor 的正则改松了、
或者复读算法失灵,这里会红。
"""
from __future__ import annotations

from app.evals.mutation import (
    BASELINE_TEXT,
    CASES,
    format_mutations,
    mutate_ai_flavor,
    mutate_repetitive,
    mutate_runaway,
    mutate_truncated,
    run_mutations,
)
from app.evals.deterministic import score_texts


def test_baseline_text_is_clean_or_mutation_test_is_meaningless():
    """基准必须干净:否则「退化前后对比」没有意义(两边都脏)。

    基准全绿(复读 0)才说明后面的「变坏」真是注入造成的。
    """
    run = score_texts([BASELINE_TEXT])
    assert run.aggregate["within_repeats_total"] == 0
    assert float(run.aggregate["mean_flavor"] or 0) < 1.0


def test_all_mutations_are_detected():
    """核心断言:每种退化都要被检出。少一个都说明评测有盲区。"""
    results = run_mutations()
    undetected = [r.name for r in results if not r.detected]
    assert not undetected, f"这些退化没被检出(评测盲区):{undetected}"


def test_repetition_mutation_raises_repeat_count():
    base = score_texts([BASELINE_TEXT]).aggregate["within_repeats_total"]
    after = score_texts([mutate_repetitive(BASELINE_TEXT)]).aggregate["within_repeats_total"]
    assert after > base


def test_ai_flavor_mutation_raises_flavor_score():
    base = float(score_texts([BASELINE_TEXT]).aggregate["mean_flavor"])
    after = float(score_texts([mutate_ai_flavor(BASELINE_TEXT)]).aggregate["mean_flavor"])
    assert after > base


def test_truncated_mutation_shrinks_text():
    base = score_texts([BASELINE_TEXT]).aggregate["total_chars"]
    after = score_texts([mutate_truncated(BASELINE_TEXT)]).aggregate["total_chars"]
    assert after < base


def test_runaway_mutation_grows_text():
    base = score_texts([BASELINE_TEXT]).aggregate["total_chars"]
    after = score_texts([mutate_runaway(BASELINE_TEXT)]).aggregate["total_chars"]
    assert after > base


def test_every_case_declares_an_expectation():
    """每个用例都要声明「该被哪个指标、往哪边检出」——没有期望就没法判有效性。"""
    for case in CASES:
        metric, direction = case.expect
        assert metric, case.name
        assert direction in ("up", "down"), case.name


def test_format_mutations_reports_verdict():
    text = format_mutations(run_mutations())
    assert "判别力轨" in text
    assert "种退化被检出" in text
    assert "门槛有判别力" in text


def test_format_mutations_handles_empty():
    assert format_mutations([]) == "未跑任何退化用例。"
