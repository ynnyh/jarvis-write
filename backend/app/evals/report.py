# app/evals/report.py
# -*- coding: utf-8 -*-
"""评测结果 → Markdown:单次运行摘要,以及两次运行的逐指标对比表(标好坏方向)。

对比表的每一行都带「变好 / 变差 / 持平」,方向写死在 AGG_METRICS 里——看表的人不用
记「AI 味是越低越好还是越高越好」。表尾固定提醒采样噪声:LLM 有随机性,单次对比
只能看趋势,想下结论请同配置多跑两次看方差。
"""
from __future__ import annotations

from typing import Any

from app.evals.prompt_registry import diff_manifests

# (聚合指标键, 显示名, 方向) 方向:up=越高越好 / down=越低越好 / one=越接近 1 越好 / info=只展示
AGG_METRICS: list[tuple[str, str, str]] = [
    ("chapters_ok", "成功章数", "up"),
    ("quarantined", "被门禁隔离章数", "down"),
    ("pass_rate", "主审达标率", "up"),
    ("mean_scores.plot", "情节均分", "up"),
    ("mean_scores.prose", "文笔均分", "up"),
    ("mean_scores.pacing", "节奏均分", "up"),
    ("mean_scores.character", "人物均分", "up"),
    ("mean_scores.continuity", "连贯均分", "up"),
    ("mean_flavor", "AI 味指数均值", "down"),
    ("mean_target_ratio", "篇幅 / 目标", "one"),
    ("mean_revision_rounds", "平均回炉轮数", "down"),
    ("total_blockers", "门禁 blocker 总数", "down"),
    ("total_major", "门禁 major 总数", "down"),
    ("total_minor", "门禁 minor 总数", "down"),
    ("preflight_warnings", "写前警告总数", "down"),
    ("within_repeats_total", "章内复读句总数", "down"),
    ("repeated_sentences_cross", "跨章重复句", "down"),
    ("repeated_phrases_cross", "跨章高频短语", "down"),
    ("facts_extracted", "圣经事实总数", "info"),
    ("tokens_per_chapter", "每章 token", "down"),
    ("seconds_per_chapter", "每章耗时(秒)", "down"),
]
_SCORE_DIMS = ("plot", "prose", "pacing", "character", "continuity")


def _get(data: dict[str, Any], dotted: str):
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _delta(a, b) -> str:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return "—"
    d = b - a
    if isinstance(a, float) or isinstance(b, float):
        return f"{d:+.2f}"
    return f"{d:+d}"


def _verdict(a, b, direction: str) -> str:
    if direction == "info" or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return ""
    if direction == "one":
        a, b, direction = abs(a - 1), abs(b - 1), "down"
    if abs(b - a) < 1e-9:
        return "持平"
    better = b > a if direction == "up" else b < a
    return "✅ 变好" if better else "❌ 变差"


def _models_line(models: dict[str, Any] | None) -> str:
    if not models:
        return "—"
    parts = []
    for tier, cfg in models.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("error"):
            parts.append(f"{tier}=错误")
            continue
        parts.append(f"{tier}={cfg.get('model') or '?'}@{cfg.get('host') or '?'}")
    return ",".join(parts) or "—"


def run_markdown(run: dict[str, Any]) -> str:
    agg = run.get("aggregate") or {}
    usage = run.get("usage") or {}
    lines = [
        f"# 评测运行 `{run.get('label')}` · 夹具《{run.get('fixture_title') or run.get('fixture')}》",
        "",
        f"- 提交:`{run.get('git_commit')}`  ·  prompt 指纹:`{run.get('prompt_fingerprint')}`",
        f"- 模型:{_models_line(run.get('models'))}",
        f"- 开始:{run.get('started_at')}  ·  总耗时 {_fmt(run.get('seconds'))} 秒",
        f"- 用量:{usage.get('calls', 0)} 次调用,"
        f"{usage.get('prompt_tokens', 0)} 入 / {usage.get('completion_tokens', 0)} 出 token",
        "",
        "## 聚合指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for key, label, _direction in AGG_METRICS:
        lines.append(f"| {label} | {_fmt(_get(agg, key))} |")
    lines += [
        "",
        "## 逐章",
        "",
        "| 章 | 标题 | 字数 | 篇幅比 | 情节/文笔/节奏/人物/连贯 | 达标 | 回炉 | 门禁 b/M/m | AI 味 | 状态 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for ch in run.get("chapters") or []:
        if not ch.get("ok"):
            lines.append(
                f"| {ch.get('n')} | {ch.get('title', '')} | — | — | — | — | — | — | — | "
                f"失败:{ch.get('error', '')[:80]} |"
            )
            continue
        review = ch.get("review") or {}
        scores = review.get("scores") or {}
        gate = ch.get("gate") or {}
        score_text = "/".join(_fmt(scores.get(d)) for d in _SCORE_DIMS)
        lines.append(
            f"| {ch['n']} | {ch.get('title', '')} | {ch.get('chars')} | {_fmt(ch.get('target_ratio'))} "
            f"| {score_text} | {_fmt(review.get('passed'))} | {_fmt(review.get('revision_rounds'))} "
            f"| {gate.get('blocker', 0)}/{gate.get('major', 0)}/{gate.get('minor', 0)} "
            f"| {_fmt((ch.get('flavor') or {}).get('score'))} | {ch.get('status')} |"
        )
    cross = run.get("cross") or {}
    tops = cross.get("repeated_sentences_top") or []
    if tops:
        lines += ["", "## 跨章重复句(前 5)", ""]
        lines += [f"- ×{t['count']}:{t['text']}" for t in tops]
    return "\n".join(lines) + "\n"


def compare_markdown(a: dict[str, Any], b: dict[str, Any]) -> str:
    """A(基线)vs B(新)。"""
    agg_a, agg_b = a.get("aggregate") or {}, b.get("aggregate") or {}
    lines = [
        f"# 评测对比:`{a.get('label')}` → `{b.get('label')}`",
        "",
        f"- 夹具:《{a.get('fixture_title') or a.get('fixture')}》"
        + ("" if a.get("fixture") == b.get("fixture") else f" ⚠️ 两次夹具不同(B={b.get('fixture')}),数字不可比"),
        f"- 提交:`{a.get('git_commit')}` → `{b.get('git_commit')}`",
        f"- 模型:A {_models_line(a.get('models'))}  ·  B {_models_line(b.get('models'))}",
    ]
    fp_a, fp_b = a.get("prompt_fingerprint"), b.get("prompt_fingerprint")
    if fp_a == fp_b:
        lines.append(f"- prompt 指纹一致(`{fp_a}`):差异来自模型 / 判据 / 采样噪声,不是 prompt")
    else:
        diff = diff_manifests(a.get("prompt_manifest") or {}, b.get("prompt_manifest") or {})
        lines.append(f"- prompt 指纹 `{fp_a}` → `{fp_b}`,变动的 prompt:")
        for kind, label in (("changed", "改动"), ("added", "新增"), ("removed", "删除")):
            for name in diff[kind]:
                lines.append(f"  - {label}:`{name}`")
    lines += [
        "",
        "| 指标 | A | B | Δ | 判定 |",
        "|---|---|---|---|---|",
    ]
    for key, label, direction in AGG_METRICS:
        va, vb = _get(agg_a, key), _get(agg_b, key)
        lines.append(
            f"| {label} | {_fmt(va)} | {_fmt(vb)} | {_delta(va, vb)} | {_verdict(va, vb, direction)} |"
        )
    lines += [
        "",
        "> LLM 生成有随机性:单次对比只看趋势;要下「变好了」的结论,请同一配置至少跑两次看方差,"
        "且以主审四维 + 门禁 + AI 味三类一起看,别只盯一格。",
    ]
    return "\n".join(lines) + "\n"
