# app/evals/thresholds.py
# -*- coding: utf-8 -*-
"""评测回归门槛:把「改坏了」变成 CI 能拦下的硬失败。

为什么需要它:
`examples/README.md` 里两套 baseline 都明确声明「不作回归基准」,于是
CONTRIBUTING 的「动 prompt 前先跑评测」只是一条口头纪律——跑不跑、比不比、
退化了多少全靠自觉。评测结果也只落 JSON 文件,没有趋势、没有门槛。

本模块把 real baseline 的关键指标固化成门槛,供 `python -m app.evals gate`
对比,不达标即非 0 退出(可直接挂 CI)。

门槛选取的两条原则(与「质量分脱虚向实」一致):
  1. **只锁确定性指标**——AI 味指数、复读数、事实抽取数、blocker 数、篇幅比,
     这些是正则/统计算出来的,跑两次结果一致。
     主审四维(plot/prose/pacing/character)是 LLM 自评且默认同模型自审,
     方差大、乐观偏差明显,**不设为硬门槛**,只作为参考随报告输出。
  2. **留合理余量**——门槛取「明显退化」而非「轻微波动」。基线 AI 味 4.1,
     门槛 6.0:允许模型正常波动,但挡住「去味 prompt 被删掉」这类真退化。
     宁可放过一次小退步,也不要让 CI 天天假报警(假报警的门禁会被绕过)。

`facts_extracted` 这一项特别针对抽取降级:章后事实抽取一旦静默失败,
圣经就不再更新,长程一致性会缓慢失血而不报错——这一项掉下来就是硬失败。
"""
from __future__ import annotations

from typing import Any

# 门槛表:值从 baseline-real-po_feng_ji(deepseek-v4-pro 全 10 章实跑)出发定。
# 想调门槛改这里,并在 PR 里说明「为什么放宽」——放宽门槛等于承认能力退化。
THRESHOLDS: dict[str, dict[str, float]] = {
    # 门禁:带硬矛盾的章节一律不许流出(这是「不崩」的底线)
    "total_blockers": {"max": 0},
    "quarantined": {"max": 1},
    # 达标率:10 章里至少 8 章一次通过
    "pass_rate": {"min": 0.8},
    # AI 味指数(确定性算法):基线 4.1,放行到 6.0
    "mean_flavor": {"max": 6.0},
    # 章内复读:基线 0,放行到 5
    "within_repeats_total": {"max": 5},
    # 事实抽取闭环:基线 55 条。掉到 30 以下说明抽取大面积失败/降级——
    # 圣经会停止生长,后续章的一致性对照失去事实源,这是最危险的静默退化。
    "facts_extracted": {"min": 30},
    # 篇幅比:基线 1.03。太低=没写够,太高=收不住(字数守卫失效)
    "mean_target_ratio": {"min": 0.7, "max": 1.4},
}

# 只作参考、不设门槛的指标(自评维度,方差大)
ADVISORY_ONLY = ("plot", "prose", "pacing", "character", "continuity")


def _get(agg: dict[str, Any], key: str) -> Any:
    if key in agg:
        return agg[key]
    mean_scores = agg.get("mean_scores") or {}
    return mean_scores.get(key)


def check_run(run: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """比对一次 run 与门槛。返回 (是否通过, 违规明细)。

    缺失的指标不判违规(不同版本可能少字段),但会在明细里标 missing,
    便于发现「指标算不出来了」这种更严重的退化。
    """
    agg = run.get("aggregate") or {}
    violations: list[dict[str, Any]] = []
    for key, bounds in THRESHOLDS.items():
        value = _get(agg, key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            violations.append({
                "metric": key, "value": value, "rule": "numeric",
                "detail": "指标不是数值,无法判定",
            })
            continue
        if "min" in bounds and value < bounds["min"]:
            violations.append({
                "metric": key, "value": value, "rule": f">= {bounds['min']}",
                "detail": f"{key} 低于下限({value} < {bounds['min']})",
            })
        if "max" in bounds and value > bounds["max"]:
            violations.append({
                "metric": key, "value": value, "rule": f"<= {bounds['max']}",
                "detail": f"{key} 超过上限({value} > {bounds['max']})",
            })
    # 指标缺失单独提示:算不出来比数值差更值得警惕
    missing = [k for k in THRESHOLDS if _get(agg, k) is None]
    for k in missing:
        violations.append({
            "metric": k, "value": None, "rule": "present",
            "detail": f"{k} 缺失(指标没算出来,可能是链路断了)",
        })
    return not violations, violations


def format_violations(violations: list[dict[str, Any]]) -> str:
    """把违规明细渲染成给人看的几行字。"""
    if not violations:
        return "全部指标在门槛内。"
    lines = [f"  ✗ {v['detail']}" for v in violations]
    return "\n".join(lines)
