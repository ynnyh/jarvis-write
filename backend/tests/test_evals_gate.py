# tests/test_evals_gate.py
# -*- coding: utf-8 -*-
"""评测回归门槛 + 判别力(mutation)验证。

两件事:
1. 真实基线(baseline-real-po_feng_ji,deepseek-v4-pro 全 10 章实跑)必须过门槛——
   否则门槛是拍脑袋定的,一上来就把已知的好结果判死。
2. **判别力**:故意把指标改坏(mutation),门槛必须拦下。
   过去评测底座从没验过「改坏了指标会不会掉」,于是「跑评测」只是一次仪式——
   指标对退化不敏感的话,跑与不跑没区别。这里把几种典型退化逐个注入,
   断言门槛确实挡得住。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

BASELINE = (
    Path(__file__).resolve().parent.parent
    / "app" / "evals" / "examples" / "baseline-real-po_feng_ji.json"
)


def _baseline_run() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _mutate(**overrides) -> dict:
    """在真实基线之上改坏若干 aggregate 指标。"""
    run = copy.deepcopy(_baseline_run())
    run["aggregate"].update(overrides)
    return run


# ---------- 1. 真实基线必须过门槛 ----------
def test_real_baseline_passes_gate():
    from app.evals.thresholds import check_run

    ok, violations = check_run(_baseline_run())
    assert ok, f"真实基线应通过门槛,实际违规:\n{violations}"


# ---------- 2. 判别力:典型退化必须被拦下 ----------
def test_gate_catches_ai_flavor_regression():
    """去味 prompt 被删/改坏 → AI 味飙升(4.1 → 9.5)必须拦下。"""
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(mean_flavor=9.5))
    assert not ok
    assert any(v["metric"] == "mean_flavor" for v in violations)


def test_gate_catches_blockers_leaking():
    """一致性门禁失效 → 带硬矛盾的章节流出来,必须拦下(这是「不崩」的底线)。"""
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(total_blockers=3, quarantined=3))
    assert not ok
    assert any(v["metric"] == "total_blockers" for v in violations)


def test_gate_catches_silent_extraction_degradation():
    """抽取静默降级 → 事实数断崖(55 → 4)必须拦下。

    这是最危险的一类退化:章节照常生成、界面照常显示「已完成」,
    但故事圣经停止生长,后续章的一致性对照悄悄失去事实源。
    """
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(facts_extracted=4))
    assert not ok
    assert any(v["metric"] == "facts_extracted" for v in violations)


def test_gate_catches_repetition_regression():
    """复读检测失效 → 章内复读暴增(0 → 40)必须拦下。"""
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(within_repeats_total=40))
    assert not ok
    assert any(v["metric"] == "within_repeats_total" for v in violations)


def test_gate_catches_word_count_guard_failure():
    """字数守卫失效 → 篇幅比失控(1.03 → 0.4,只写了一半)必须拦下。"""
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(mean_target_ratio=0.4))
    assert not ok
    assert any(v["metric"] == "mean_target_ratio" for v in violations)


def test_gate_flags_missing_metrics():
    """指标算不出来了(指标缺失)比数值越界更值得警惕,必须显式报出来。"""
    from app.evals.thresholds import check_run

    run = _baseline_run()
    run["aggregate"].pop("facts_extracted")
    ok, violations = check_run(run)
    assert not ok
    assert any(v["metric"] == "facts_extracted" and v["value"] is None for v in violations)


def test_gate_ignores_advisory_dimensions():
    """主审四维(LLM 自评、方差大)不设硬门槛:同模型自审的分数不该当门禁。

    四维从 7.x 掉到 5.x 是可疑的,但不该由确定性门槛判死——
    它由 compare 报告呈现,交人判断。
    """
    from app.evals.thresholds import check_run

    ok, violations = check_run(_mutate(
        mean_scores={"plot": 5.0, "prose": 5.0, "pacing": 5.0, "character": 5.0, "continuity": 5.0}
    ))
    assert ok, f"自评维度不应触发硬门槛,实际={violations}"


# ---------- 3. CLI 退出码(可挂 CI) ----------
def test_gate_cli_exit_codes():
    from app.evals.__main__ import main

    assert main(["gate", str(BASELINE)]) == 0, "真实基线应 0 退出"
    assert main(["gate", "--list"]) == 0
