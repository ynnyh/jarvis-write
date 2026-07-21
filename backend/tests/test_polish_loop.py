# tests/test_polish_loop.py
# -*- coding: utf-8 -*-
"""润色"先诊断后治疗"闭环(mock LLM,无需 API key)。

验证点:
- 检测驱动定点改写:润色 prompt 里贴了 ai_flavor_report 的命中句 + 类别
- 两段式输出契约:prompt 含【诊断】/【策略】/【润色稿】标记
- 输出解析:只取【润色稿】之后的文本;片段润色的 notes 带回诊断
- 前后对比报告带分类明细;事实锁定流程不受影响
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.engines.polish import polisher

AI_FRAGMENT = "她眼中闪过一丝慌乱,嘴角勾起一抹弧度。空气仿佛凝固了。总而言之,他赢了。"


class _LoopAdapter:
    """按 prompt 内容分发:抽事实/校验返回 JSON,润色返回两段式;记录所有 prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        if "抽取" in prompt and "事实" in prompt:
            return '{"facts": ["她赢了"]}'
        if "对比" in prompt and "校验" not in prompt or "逐条检查" in prompt:
            return '{"violations": []}'
        return (
            "【诊断】1. [万能神态套话] 她眼中闪过一丝慌乱\n"
            "【策略】1. 神态套话换成具体动作\n"
            "【润色稿】她睫毛颤了一下,别过脸去。他赢了。"
        )


def test_polish_text_injects_hits_and_parses_output():
    adapter = _LoopAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        result = asyncio.run(polisher.polish_text(AI_FRAGMENT))

    polish_prompt = next(p for p in adapter.prompts if "待润色文本" in p)
    # 检测驱动:命中句 + 类别贴进 prompt,且要求"其余好句子保持"
    assert "万能神态套话" in polish_prompt
    assert "眼中闪过一丝" in polish_prompt
    assert "针对这些具体命中点修改" in polish_prompt
    # 两段式契约注入
    assert "【诊断】" in polish_prompt and "【策略】" in polish_prompt
    assert "【润色稿】" in polish_prompt
    # 输出解析:只取【润色稿】之后,诊断不进正文
    assert result["polished"] == "她睫毛颤了一下,别过脸去。他赢了。"
    # 事实锁定流程仍在:抽事实 + 校验两轮都发生过
    assert result["locked_facts"] == ["她赢了"]
    assert result["violations"] == []
    # 前后对比报告带分类明细,润色后得分下降
    assert result["flavor_before"]["categories"]["万能神态套话"]["count"] >= 1
    assert result["flavor_after"]["score"] < result["flavor_before"]["score"]


def test_polish_fragment_injects_hits_and_notes_carry_diagnosis():
    adapter = _LoopAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        result = asyncio.run(
            polisher.polish_fragment(AI_FRAGMENT, "更紧张一些", "主角对决")
        )

    prompt = adapter.prompts[0]
    assert "眼中闪过一丝" in prompt and "万能神态套话" in prompt
    assert "【诊断】" in prompt and "【润色稿】" in prompt
    assert result["polished"] == "她睫毛颤了一下,别过脸去。他赢了。"
    assert result["notes"] and "【诊断】" in result["notes"]


def test_split_polish_output_fallback_without_marker():
    """模型没按契约输出(无【润色稿】标记)时,整段当润色稿,不报错。"""
    polished, diagnosis = polisher._split_polish_output("就是一段润色后的正文。")
    assert polished == "就是一段润色后的正文。"
    assert diagnosis is None
