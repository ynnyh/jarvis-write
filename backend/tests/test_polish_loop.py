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
from app.prompts.style_capsules import render_voice_block

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


# ---------- 选区 craft 微工具(描写 / 扩写 / 头脑风暴) ----------


class _CraftAdapter:
    """记录 prompt,按需返回。默认回改写稿;brainstorm 用例单独指定分行点子。"""

    def __init__(self, reply: str = "他迈步跨过高大的城门,青砖在脚下发出闷响。"):
        self.prompts: list[str] = []
        self.reply = reply

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_craft_describe_returns_rewrite_and_injects_context():
    """describe:返回 rewrite(ideas 为空);意图/规则/去AI腔/蓝图/补充要求都进 prompt。"""
    adapter = _CraftAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        result = asyncio.run(
            polisher.craft_fragment("他走进城门。", "describe", "多点听觉", "主角进城")
        )
    assert result == {
        "mode": "describe",
        "rewrite": "他迈步跨过高大的城门,青砖在脚下发出闷响。",
        "ideas": None,
        "notes": None,
    }
    prompt = adapter.prompts[0]
    assert "画面感与感官细节" in prompt  # describe 的 intro
    assert "不推进新剧情" in prompt  # craft 专属规则(区别于润色的"篇幅相当")
    assert "去除 AI 腔" in prompt  # describe 追加去 AI 腔规则
    assert "主角进城" in prompt and "多点听觉" in prompt  # 蓝图摘要 + 补充要求
    assert "他走进城门。" in prompt


def test_craft_expand_returns_rewrite():
    """expand:同样走改写稿链路,intro 换成扩写。"""
    adapter = _CraftAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        result = asyncio.run(polisher.craft_fragment("他走进城门。", "expand"))
    assert result["mode"] == "expand"
    assert result["rewrite"] == "他迈步跨过高大的城门,青砖在脚下发出闷响。"
    assert result["ideas"] is None
    assert "扩写" in adapter.prompts[0] or "写透" in adapter.prompts[0]


def test_craft_brainstorm_returns_ideas_no_deai_no_rewrite():
    """brainstorm:解析分行点子成列表,rewrite 为空;不注入去 AI 腔规则(不改正文)。"""
    adapter = _CraftAdapter(
        "- 让城门守卫认出他,埋一句旧怨\n- 城内飘来血腥味,预示后文\n2. 他摸了摸剑柄,犹豫要不要进"
    )
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        result = asyncio.run(polisher.craft_fragment("他走进城门。", "brainstorm"))
    assert result["mode"] == "brainstorm"
    assert result["rewrite"] is None
    assert result["ideas"] == [
        "让城门守卫认出他,埋一句旧怨",
        "城内飘来血腥味,预示后文",
        "他摸了摸剑柄,犹豫要不要进",
    ]
    assert "去除 AI 腔" not in adapter.prompts[0]  # 不改正文,不注入去 AI 腔块


def test_craft_unknown_mode_and_empty_raise():
    """未知模式 / 空片段 → ValueError(端点据此转 400)。"""
    import pytest

    adapter = _CraftAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        with pytest.raises(ValueError):
            asyncio.run(polisher.craft_fragment("他走进城门。", "translate"))
        with pytest.raises(ValueError):
            asyncio.run(polisher.craft_fragment("   ", "describe"))


def test_parse_ideas_strips_bullets_dedup_cap():
    """点子解析:去各式行首标记、去空行、最多 6 条;无分行退化为单条。"""
    ideas = polisher._parse_ideas("- a\n* b\n• c\n1. d\n2、e\n3)f\n  \ng\nh")
    assert ideas == ["a", "b", "c", "d", "e", "f"]  # 截断到 6
    assert polisher._parse_ideas("就一整段没分行") == ["就一整段没分行"]


# ---------- 去味自愈闭环(deai_self_heal / deai_rewrite,mock LLM) ----------

DEAI_DIRTY = (
    "她眼中闪过一丝不易察觉的慌乱,嘴角勾起一抹弧度。空气仿佛凝固了,时间仿佛静止了。"
    "他沉默片刻,微微一笑。他轻声说道,语气平静。他缓缓开口,目光如炬。"
    "首先,他渴望自由。其次,他恐惧未知。最后,一切都已注定。"
    "综上所述,命运的齿轮开始转动。总而言之,这个故事告诉我们,勇气终将战胜恐惧。"
)
DEAI_CLEAN = (
    "老张把烟头摁灭在墙上,说走吧。巷子里没人。风把门带上,咣当一声。"
    "他数了数兜里的钱,七块,够一碗面,不够一杯酒。面摊的灯泡晃了一下。"
    "老板娘问他还加蛋吗,他摇头。雨下起来了,先是几滴,跟着砸在铁皮棚上噼啪响。"
    "他把衣领竖起来,走进雨里,身后的灯一盏一盏灭了。"
)


class _DeaiAdapter:
    """按序返回预置回复,记录收到的 prompt。"""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


def test_deai_self_heal_clean_text_short_circuits():
    """干净文本(score≤门槛)直接返回,一次 LLM 都不调。"""
    adapter = _DeaiAdapter(DEAI_DIRTY)  # 就算备了回复也不该被取用
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        text, before, after = asyncio.run(polisher.deai_self_heal(DEAI_CLEAN))
    assert text == DEAI_CLEAN
    assert before.score == after.score
    assert adapter.prompts == []  # 没调用过 LLM


def test_deai_self_heal_adopts_rewrite_when_score_drops():
    """超标文本 → 重写降分 → 采纳重写稿。"""
    adapter = _DeaiAdapter(DEAI_CLEAN)
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        text, before, after = asyncio.run(polisher.deai_self_heal(DEAI_DIRTY))
    assert before.score > polisher.DEAI_GATE_SCORE  # 确实触发了自愈
    assert text == DEAI_CLEAN
    assert after.score < before.score
    assert len(adapter.prompts) == 1


def test_deai_self_heal_reverts_when_not_improved():
    """重写没降分(返回同样脏)→ 丢弃回退,保留原文,不硬撑。"""
    adapter = _DeaiAdapter(DEAI_DIRTY)  # 回复和原文一样脏
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        text, before, after = asyncio.run(polisher.deai_self_heal(DEAI_DIRTY))
    assert text == DEAI_DIRTY           # 回退到原文
    assert after.score == before.score
    assert len(adapter.prompts) == 1    # 试一轮没改好即停


def test_deai_self_heal_reverts_when_length_out_of_range():
    """重写篇幅越界(缩水过多)→ 视为跑偏,丢弃回退。"""
    adapter = _DeaiAdapter("他赢了。")  # 远短于原文
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        text, before, after = asyncio.run(polisher.deai_self_heal(DEAI_DIRTY))
    assert text == DEAI_DIRTY
    assert len(adapter.prompts) == 1


def test_deai_rewrite_prompt_injects_hits_pairwise_and_style():
    """定向重写 prompt:命中句 + 配对反例 + 文风范本(style_block)都注入。"""
    adapter = _DeaiAdapter(DEAI_CLEAN)
    report = polisher.ai_flavor_report(DEAI_DIRTY)
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        asyncio.run(
            polisher.deai_rewrite(DEAI_DIRTY, report, style_block="【文风范本】学汪曾祺")
        )
    prompt = adapter.prompts[0]
    assert "眼中闪过一丝" in prompt           # 命中句
    assert "✗" in prompt and "✓" in prompt    # 配对反例
    assert "【文风范本】学汪曾祺" in prompt     # 正向锚 style_block
    assert "定向去味" in prompt


def test_strip_rewrite_meta_removes_fences():
    assert polisher._strip_rewrite_meta("```\n正文\n```") == "正文"
    assert polisher._strip_rewrite_meta("```markdown\n正文\n```") == "正文"
    assert polisher._strip_rewrite_meta("  正文  ") == "正文"


# ---------- ④ 正向锚(voice)铺到各正文入口:片段润色 / craft / 整章润色 ----------

PLAIN_VOICE_MARK = "母亲把最后一件毛衣叠好"  # plain 胶囊 sample 的可辨识子串


def test_polish_fragment_injects_voice_block():
    """片段润色接受 voice_block,文风范本正向锚进 prompt。"""
    adapter = _LoopAdapter()
    voice = render_voice_block(voice_key="plain")
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        asyncio.run(polisher.polish_fragment(AI_FRAGMENT, voice_block=voice))
    assert "文风范本" in adapter.prompts[0]
    assert PLAIN_VOICE_MARK in adapter.prompts[0]


def test_craft_describe_injects_voice_brainstorm_does_not():
    """craft:describe 注入 voice(新增文字也要对味);brainstorm 不改正文,不注入。"""
    voice = render_voice_block(voice_key="plain")

    a1 = _CraftAdapter()
    with patch.object(polisher, "get_adapter_for", return_value=a1):
        asyncio.run(
            polisher.craft_fragment("他走进城门。", "describe", voice_block=voice)
        )
    assert PLAIN_VOICE_MARK in a1.prompts[0]  # describe 吃 voice

    a2 = _CraftAdapter("- 点子一\n- 点子二")
    with patch.object(polisher, "get_adapter_for", return_value=a2):
        asyncio.run(
            polisher.craft_fragment("他走进城门。", "brainstorm", voice_block=voice)
        )
    assert PLAIN_VOICE_MARK not in a2.prompts[0]  # brainstorm 不改正文,不注入


def test_polish_text_injects_voice_from_global_tendency():
    """整章润色:从 global_tendency 的创作档案自动补 voice(API 层零改动即生效)。"""
    adapter = _LoopAdapter()
    gt = {"_profile": {"voice_key": "plain"}}
    with patch.object(polisher, "get_adapter_for", return_value=adapter):
        asyncio.run(polisher.polish_text(AI_FRAGMENT, global_tendency=gt))
    polish_prompt = next(p for p in adapter.prompts if "待润色文本" in p)
    assert "文风范本" in polish_prompt
    assert PLAIN_VOICE_MARK in polish_prompt
