# tests/test_evals_report.py
# -*- coding: utf-8 -*-
"""评测底座·报告:run_markdown 与 compare_markdown 的方向判定、表格完整性。

不跑真 LLM,只测把现成的 run dict 渲染成 Markdown 后该有的字段都在。
"""
from __future__ import annotations

from app.evals.report import (
    AGG_METRICS,
    compare_markdown,
    run_markdown,
    _verdict,
)


def _fake_run(label: str, *, chapters_passed: list[bool], flavor: float, repeats: int) -> dict:
    return {
        "label": label,
        "fixture": "po_feng_ji",
        "fixture_title": "破封纪",
        "git_commit": "abc1234",
        "prompt_fingerprint": "fp_abc",
        "prompt_manifest": {},
        "started_at": "2026-09-04T09:00:00",
        "seconds": 100.0,
        "models": {
            "quality": {"model": "m1", "host": "api.example.com"},
            "fast": {"model": "m1", "host": "api.example.com"},
            "review": {"model": "m1", "host": "api.example.com"},
        },
        "chapters": [
            {
                "n": i + 1,
                "title": f"第{i + 1}章",
                "ok": True,
                "seconds": 12.3,
                "status": "finalized",
                "quarantined": False,
                "chars": 2500,
                "target_ratio": 1.0,
                "paragraphs": 22,
                "dialogue_ratio": 0.21,
                "within_repeats": repeats,
                "flavor": {"score": flavor + 0.05 * i},
                "review": {
                    "scores": {"plot": 7, "prose": 7, "pacing": 6, "character": 7, "continuity": 7},
                    "passed": chapters_passed[i],
                    "revision_rounds": 1,
                    "repair_rounds": 0,
                    "comment": "ok",
                },
                "gate": {"blocker": 0, "major": 1, "minor": 2},
                "preflight_warnings": 0,
                "guard_action": "none",
                "extraction": {"entities": {"count": 4}, "facts": {"count": 7}},
            }
            for i in range(len(chapters_passed))
        ],
        "cross": {"repeated_sentences": 0, "repeated_phrases": 0},
        "bible": {"entities": 7, "facts": 12, "foreshadowings": 3},
        "usage": {"calls": 14, "prompt_tokens": 3000, "completion_tokens": 2200},
    }


def _attach_aggregate(run: dict) -> dict:
    """把 aggregator() 跑一次 — 这里不能直接 import,会绕开 settings;手算。"""
    chapters = run["chapters"]
    n = len(chapters)
    run["aggregate"] = {
        "chapters_total": n,
        "chapters_ok": n,
        "quarantined": 0,
        "pass_rate": sum(1 for c in chapters if c["review"]["passed"]) / n,
        "mean_scores": {
            d: sum(c["review"]["scores"][d] for c in chapters) / n for d in ("plot", "prose", "pacing", "character", "continuity")
        },
        "mean_flavor": sum(c["flavor"]["score"] for c in chapters) / n,
        "mean_target_ratio": sum(c["target_ratio"] for c in chapters) / n,
        "mean_revision_rounds": sum(c["review"]["revision_rounds"] for c in chapters) / n,
        "total_blockers": sum(c["gate"]["blocker"] for c in chapters),
        "total_major": sum(c["gate"]["major"] for c in chapters),
        "total_minor": sum(c["gate"]["minor"] for c in chapters),
        "preflight_warnings": sum(c["preflight_warnings"] for c in chapters),
        "within_repeats_total": sum(c["within_repeats"] for c in chapters),
        "repeated_sentences_cross": run["cross"]["repeated_sentences"],
        "repeated_phrases_cross": run["cross"]["repeated_phrases"],
        "facts_extracted": run["bible"]["facts"],
        "tokens_per_chapter": 2600,
        "seconds_per_chapter": 12,
    }
    return run


def test_run_markdown_includes_all_agg_metric_labels():
    run = _attach_aggregate(_fake_run("baseline", chapters_passed=[True, True], flavor=2.3, repeats=0))
    md = run_markdown(run)
    # 全部 AGG_METRICS 显示名都得出现
    for _key, label, _direction in AGG_METRICS:
        assert label in md
    # 关键 heading
    assert "破封纪" in md
    assert "baseline" in md
    # target_ratio 以 0.x 或 1.x 形式出现至少一次
    assert any(line.startswith("|") and ("0.98" in line or "1.0" in line or "1.1" in line) for line in md.splitlines())


def test_run_markdown_renders_failed_chapter_row():
    run = _attach_aggregate(_fake_run("baseline", chapters_passed=[True, True], flavor=2.3, repeats=0))
    run["chapters"].append({
        "n": 3, "title": "裂纹复发", "ok": False, "seconds": 0.5,
        "error": "RateLimitError: too many requests",
    })
    md = run_markdown(run)
    # 失败章节用「失败:」前缀占位,不报 KeyError
    assert "失败" in md
    assert "too many requests" in md


def test_compare_markdown_judges_lower_ai_flavor_as_better():
    a = _attach_aggregate(_fake_run("baseline", chapters_passed=[True, True], flavor=5.0, repeats=5))
    b = _attach_aggregate(_fake_run("new", chapters_passed=[True, True], flavor=2.5, repeats=2))
    md = compare_markdown(a, b)
    # AI 味 down(越低越好),从 5 → 2.5 是变好;章内复读总数从 5 → 2 是变好
    assert "AI 味指数均值" in md
    assert md.count("✅ 变好") >= 2


def test_compare_markdown_judges_higher_pass_rate_as_better():
    # a 第一章没过,b 两章都过 → pass_rate 从 0.5 → 1.0 变好
    a = _attach_aggregate(_fake_run("baseline", chapters_passed=[False, True], flavor=3.0, repeats=3))
    b = _attach_aggregate(_fake_run("new", chapters_passed=[True, True], flavor=3.0, repeats=3))
    md = compare_markdown(a, b)
    assert "主审达标率" in md
    # B 应该显示 ✅ 变好
    assert "✅ 变好" in md


def test_verdict_classifies_direction_correctly():
    # up:高好
    assert _verdict(5, 7, "up") == "✅ 变好"
    assert _verdict(7, 5, "up") == "❌ 变差"
    assert _verdict(5, 5, "up") == "持平"
    # down:低好
    assert _verdict(7, 5, "down") == "✅ 变好"
    assert _verdict(5, 7, "down") == "❌ 变差"
    # one:接近 1 好
    assert _verdict(0.7, 0.95, "one") == "✅ 变好"
    assert _verdict(0.95, 0.7, "one") == "❌ 变差"
