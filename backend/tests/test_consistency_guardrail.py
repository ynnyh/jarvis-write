# tests/test_consistency_guardrail.py
# -*- coding: utf-8 -*-
"""一致性门禁 severity 护栏 + prose 定向重写指令的回归。

线上事故(2026-08-29):门禁模型把「手上伤口回现实后没交代」的细节标成 blocker
(描述里自己都写着「尚可解释为次日早晨」),blocker 一票否决 → 每章必烧满回炉
预算(3/3);另一条自认「不属于与已确立事实的冲突」的新设定也标了 major。
钉住:模型打圆场的措辞出现即降级;prose 未达标时重写指令必须带具体禁则。
"""
from __future__ import annotations

from app.engines.consistency.checker import _normalize_issue
from app.engines.pipeline.chapter import _with_prose_directive


def _issue(severity: str, description: str, suggestion: str = "") -> dict:
    return {"severity": severity, "type": "state", "description": description,
            "suggestion": suggestion}


def test_hedged_blocker_downgrades_to_major():
    """描述里自己打圆场(「尚可解释」)的 blocker → 降为 major,不配一票否决。"""
    raw = _issue(
        "blocker",
        "上一章章末已入睡,本章开头直接醒来,衔接不自然,但尚可解释为次日早晨;"
        "指甲断裂未在后续体现,建议在返回现实后补充一句手部状态。",
        "在返回现实后补充一句手部状态。",
    )
    raw["evidence"] = "他低头看自己的手,指甲断了。"  # 逐字举证成立,只测措辞护栏
    issue = _normalize_issue(raw, "他低头看自己的手,指甲断了。")
    assert issue["severity"] == "major"


def test_self_admitted_non_conflict_downgrades_to_minor():
    """描述里自认「不属于与已确立事实的冲突」(新设定引入)→ 最多 minor。"""
    issue = _normalize_issue(_issue(
        "major",
        "林薇声称自己也是人格碎片,此为新引入的设定,不属于与已确立事实的冲突,"
        "需确认是否与后续设定兼容。",
    ), "林薇说:我也是碎片。")
    assert issue["severity"] == "minor"


def test_genuine_blocker_stays_blocker():
    """真硬矛盾(无打圆场措辞,逐字举证成立)保持 blocker,不许护栏误伤。"""
    raw = _issue(
        "blocker",
        "主角在第 3 章已经死亡,本章却活着出场并参与战斗,与已确立事实直接冲突。",
        "删除该角色或改为回忆闪回。",
    )
    raw["evidence"] = "他活着出场并参与战斗。"
    assert _normalize_issue(raw, "他活着出场并参与战斗。")["severity"] == "blocker"


def test_blocker_without_evidence_downgrades_to_minor():
    """举证失败(引不到正文)的 blocker → 降 minor:幻觉举证无一票否决资格。"""
    raw = _issue(
        "blocker",
        "主角在第 3 章已经死亡,本章却活着出场并参与战斗,与已确立事实直接冲突。",
        "删除该角色或改为回忆闪回。",
    )
    assert _normalize_issue(raw, "他活着出场并参与战斗。")["severity"] == "minor"


def test_minor_stays_minor_even_with_markers():
    minor = _normalize_issue(_issue(
        "minor", "时间推进合理,无矛盾。建议在开头加一句「第二天早上」。"), "窗外天亮了。")
    assert minor["severity"] == "minor"


def test_prose_directive_appended_only_when_below_threshold():
    """prose < 阈值 → 重写指令追加去 AI 腔禁则;达标或脏值 → 原样返回。"""
    directive = "主编总评:整体尚可。"
    out = _with_prose_directive(directive, {"prose": 6}, 7)
    assert "文笔硬要求" in out and "AI" not in directive  # 原指令无此禁则,追加后才有
    assert out.startswith(directive)

    assert _with_prose_directive(directive, {"prose": 7}, 7) == directive
    assert _with_prose_directive(directive, {"prose": None}, 7) == directive  # 脏值不追加
    assert _with_prose_directive("", {"prose": 5}, 7) == _with_prose_directive(
        "", {"prose": 5}, 7
    )
