# app/evals/deterministic.py
# -*- coding: utf-8 -*-
"""确定性轨(docs/15 §7.1 轨 A):不调 LLM 也能回答「生成质量有没有退化」。

为什么单开这一轨:
现有的 `run` / `gate` 是**质量轨**——真跑模型、真花钱、只能低峰手动跑。于是
「动 prompt 前先跑评测」永远只是一条口头纪律,PR 上没人能自动拦住退化。这一轨
把**不需要模型就能算**的那部分指标拉出来,让它在每次 CI 上都跑:

  · 对**已有的正文样本**(黄金样本落盘正文 / 仓库里的语料文件)算确定性指标;
  · 对**同一批正文**跨章算复读与高频短语;
  · 与固化门槛比对,越界即非 0 退出。

关键设计:**门槛复用 app/evals/thresholds.py**,不另造一把尺子。两轨的区别只在
「正文从哪来」——质量轨是现场生成,确定性轨是既有样本。这样「同一句话在两轨里
含义相同」,不会出现「CI 绿但评测红」。

另一个用途是**判别力轨(轨 C)的底座**:mutation test 要问「故意写坏正文,这些
指标会不会掉」——那需要的就是「给一段正文出指标 + 判门槛」这一个纯函数接口,
正是本模块 `score_texts` / `evaluate_texts` 提供的东西(见 app/evals/mutation.py)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.evals.metrics import cross_chapter_metrics, text_metrics
from app.evals.thresholds import THRESHOLDS, check_run


@dataclass
class DeterministicRun:
    """一批正文的确定性指标(与质量轨 run 的 aggregate 同名字段,便于共用门槛)。"""

    label: str = ""
    chapters: int = 0
    aggregate: dict[str, Any] = field(default_factory=dict)
    per_chapter: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def score_texts(
    texts: Iterable[str],
    *,
    label: str = "deterministic",
    target_words: int | None = None,
) -> DeterministicRun:
    """给一批正文出确定性指标,**纯函数、零 LLM、零 IO**。

    产出结构与质量轨的 run 对齐(aggregate 里是同一批键名),这样 `check_run`
    能原样复用——两轨共用一套门槛,不会各说各话。
    """
    rows = [t for t in texts]
    usable = [t for t in rows if (t or "").strip()]
    run = DeterministicRun(label=label, chapters=len(usable))

    if not usable:
        run.notes.append(
            "没有可比对的正文样本——确定性轨跳过(不是通过;空样本说明夹具/语料缺失)"
        )
        return run

    per = []
    for i, text in enumerate(usable, 1):
        m = text_metrics(text, target_words)
        per.append({"n": i, **m})
    cross = cross_chapter_metrics(usable) if len(usable) >= 2 else {}

    def _mean(values: list[Any]) -> float | None:
        nums = [float(v) for v in values if isinstance(v, (int, float))]
        return round(sum(nums) / len(nums), 2) if nums else None

    total_chars = sum(int(r["chars"]) for r in per)
    total_hanzi = sum(int(r["hanzi"]) for r in per)
    run.per_chapter = per
    run.aggregate = {
        "chapters_total": len(usable),
        "chapters_ok": len(usable),
        # 确定性轨没有主审(那要 LLM),pass_rate 置 None —— 门槛不判缺失之外的行为,
        # 但 check_run 会把缺失列出来,这正是我们想要的「两轨覆盖不同指标」的提示。
        "pass_rate": None,
        "mean_flavor": _mean([r["flavor"]["score"] for r in per]),
        "within_repeats_total": sum(int(r["within_repeats"]) for r in per),
        "repeated_sentences_cross": int(cross.get("repeated_sentences") or 0),
        "repeated_phrases_cross": int(cross.get("repeated_phrases") or 0),
        "mean_target_ratio": _mean([r.get("target_ratio") for r in per]),
        "burstiness_flagged": sum(
            1 for r in per if r["flavor"]["burstiness_flag"]
        ),
        "metronome_groups_total": sum(
            int(r["flavor"]["metronome_groups"]) for r in per
        ),
        "tail_summary_total": sum(
            int(r["flavor"]["tail_summary_count"]) for r in per
        ),
        "total_chars": total_chars,
        "total_hanzi": total_hanzi,
        "chars_per_chapter": round(total_chars / len(usable)) if usable else 0,
    }
    return run


def evaluate_texts(
    texts: Iterable[str],
    *,
    label: str = "deterministic",
    target_words: int | None = None,
) -> tuple[DeterministicRun, bool, list[dict[str, Any]]]:
    """算指标 + 比门槛。返回 (run, 是否通过, 违规明细)。

    比门槛时**只比确定性轨真正算得出来的键**——质量轨独有的指标(主审四维、
    blocker 数、事实抽取数)在这一轨本就缺席,拿它们的缺失当违规会让 CI 恒红。
    这是两轨共享「同一套门槛定义」但「各自检查自己覆盖的项」的正确做法。
    """
    run = score_texts(texts, label=label, target_words=target_words)
    if not run.chapters:
        return run, False, [{
            "metric": "(样本)", "value": 0, "rule": "present",
            "detail": "没有正文样本,无法评估——请在 fixtures 里提供语料",
        }]

    ok, violations = check_run({"aggregate": run.aggregate})
    # 只保留**本轨打算算**的指标上的违规。
    # 质量轨独有的项(主审达标率 pass_rate、blocker 数、事实抽取数)这一轨天然没有,
    # 若拿它们判「指标没算出来」,CI 会恒红(实测踩过)。所以显式列出本轨负责的键,
    # 而不是用「有没有值」反推——`pass_rate` 恰好是「键在但值恒为 None」,用值反推会漏。
    violations = [v for v in violations if v.get("metric") in DETERMINISTIC_OWNED]
    return run, (not violations), violations


# 本轨**负责**的门槛键。其余键属于质量轨(需真跑 LLM 才可能算出来),
# 在本轨缺席是正常的,不作违规判定。
DETERMINISTIC_OWNED = frozenset({
    "mean_flavor",
    "within_repeats_total",
    "mean_target_ratio",
})


# 确定性轨**负责**的门槛键(明确列出,便于 CI 日志一眼看清「这一轨管什么」)。
# 其余门槛键属于质量轨(需真跑 LLM 才可能算出来),在本轨缺席是正常的、不判违规。
DETERMINISTIC_OWNED = frozenset({
    "mean_flavor",
    "within_repeats_total",
    "mean_target_ratio",
})


def format_summary(run: DeterministicRun) -> str:
    """给人看的一行摘要(CI 日志里读)。"""
    a = run.aggregate
    if not run.chapters:
        return "确定性轨:无样本"
    checked = "、".join(
        f"{k}={a.get(k)}" for k in sorted(DETERMINISTIC_OWNED) if a.get(k) is not None
    )
    return (
        f"确定性轨:{run.chapters} 章 · 总字 {a.get('total_chars')} · "
        f"AI 味均值 {a.get('mean_flavor')} · 章内复读 {a.get('within_repeats_total')} · "
        f"跨章重复句 {a.get('repeated_sentences_cross')}\n  {checked}"
    )


def format_violations(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return "  全部确定性指标在门槛内。"
    return "\n".join(f"  ✗ {v['detail']}" for v in violations)
