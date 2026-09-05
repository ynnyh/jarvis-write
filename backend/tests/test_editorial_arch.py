# tests/test_editorial_arch.py
# -*- coding: utf-8 -*-
"""主审 prompt 的叙事架构 AI 指纹清单(sepia L1 借鉴):模板转折 Echo test /
结局三脚架 / 叙述者点题 / 情绪呈现单一化,以及校准纪律。"""
from __future__ import annotations

from app.prompts.editorial import REVIEW_PROMPT


def test_review_prompt_has_architecture_checklist():
    """L1 叙事架构检查项全部在主审 prompt 里。"""
    assert "叙述者点题" in REVIEW_PROMPT
    assert "结局三脚架" in REVIEW_PROMPT
    assert "情绪呈现单一化" in REVIEW_PROMPT
    assert "模板转折" in REVIEW_PROMPT
    assert "模糊指涉" in REVIEW_PROMPT
    assert "关系全正向" in REVIEW_PROMPT


def test_review_prompt_has_echo_test():
    """Echo test:转折点自问「重写二十次还会出现吗」。"""
    assert "重写二十次" in REVIEW_PROMPT


def test_review_prompt_has_calibration():
    """校准纪律:宁缺毋滥,全项套用本身是模板。"""
    assert "宁缺毋滥" in REVIEW_PROMPT
    assert "校准" in REVIEW_PROMPT


def test_review_prompt_guardrails():
    """反向保护:直陈情绪是人味不是问题;建议上限放宽到 0-4 条。"""
    assert "不是问题" in REVIEW_PROMPT
    assert "0-4 条" in REVIEW_PROMPT
    # 输出 JSON 契约保持不变(四维 scores + suggestions 结构)
    assert '"scores"' in REVIEW_PROMPT
    assert '"suggestions"' in REVIEW_PROMPT
