# tests/test_json_retry.py
# -*- coding: utf-8 -*-
"""LLM JSON 任务两级保险:先「续写补完」,再「整篇重发」。

背景(2026-09-08 50 章压测):中转渠道会把响应尾巴随机截断——连
`{"issues": []`(16 字符)都被截在半途,导致 9/13 章一致性检查/主审
解析失败而进隔离。

第一版保险是「解析失败原样重打一次」。50 章实测打脸:9 个隔离章重跑只救回
2 章——输出长度一字不差,等于再撞一次同样的截断概率,还白烧 87.8 万 token。
改成先续写:把已收到的半截前缀回传,让模型只补剩余部分,单次输出大幅变短,
既省钱又真正绕开「输出越长越容易被掐断」的循环。救不回来才整篇重发。

本文件锁死行为:
1. 首次成功 → 绝不多打(保险不能变成固定双倍开销);
2. 首次截断 → 走续写而非整篇重发,续写成功恰好 2 次调用;
3. 续写调用本身失败 → 不致命,降级走整篇重发;
4. 输出连 JSON 痕迹都没有 → 不浪费续写,直接整篇重发;
5. 续写与重发都救不回 → 显式错误交回调用方(不吞错、不冒充干净);
6. LLM 主调用异常原样上抛(与解析失败是两种现场)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.engines.common import _CONTINUE_INSTRUCTION

# 续写提示词的识别特征:假适配器靠它区分「主 prompt」与「续写 prompt」
_CONTINUE_MARK = "被中断的输出"


def _is_continue_prompt(prompt: str) -> bool:
    return _CONTINUE_MARK in prompt


# ---------- ask_llm_json 单元行为 ----------

def test_first_try_success_no_extra_call():
    """首次就成功 → 绝不多打(重试保险不能变成固定双倍开销)。"""

    class _GoodAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            return '{"ok": true}'

    from app.engines.common import ask_llm_json

    adapter = _GoodAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "p", label="测试"))
    assert err is None and data == {"ok": True}
    assert adapter.calls == 1


def test_continue_saves_truncated_json():
    """首次截断 → 走续写(不整篇重发):主调用 1 次 + 续写 1 次 = 2 次。"""

    class _TruncatedThenContinueAdapter:
        def __init__(self):
            self.calls = 0
            self.prompts: list[str] = []

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            self.prompts.append(prompt)
            if _is_continue_prompt(prompt):
                # 只补被掐掉的那一截
                return (
                    ' "minor", "type": "state", "description": "陆辰的伤未交代来源",'
                    ' "evidence": "伤", "conflicting_fact": "", "suggestion": "补一句来源"}]}'
                )
            return '{"issues": [{"severity":'  # 被网关掐断

    from app.engines.common import ask_llm_json

    adapter = _TruncatedThenContinueAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "原始任务 prompt", label="测试"))

    assert err is None, f"续写拼成完整 JSON 后应成功,实际错误={err}"
    issues = data["issues"]
    assert len(issues) == 1 and issues[0]["description"].startswith("陆辰")
    assert adapter.calls == 2, f"应恰好 2 次调用(主 + 续写),实际 {adapter.calls}"
    # 续写必须把已收到的半截内容回传,模型才知道从哪儿接着写
    assert _CONTINUE_MARK in adapter.prompts[1]
    assert '{"issues": [{"severity":' in adapter.prompts[1]


def test_continue_call_failure_falls_back_to_full_rerun():
    """续写调用本身抛异常 → 不致命,改走整篇重发。"""

    class _ContinueBoomThenGoodAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            if _is_continue_prompt(prompt):
                raise RuntimeError("网络超时")
            if self.calls == 1:
                return '{"issues": ['
            return '{"issues": []}'

    from app.engines.common import ask_llm_json

    adapter = _ContinueBoomThenGoodAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "p", label="测试"))
    assert err is None and data == {"issues": []}
    # 主(截断) + 续写(炸) + 重发主(成功) = 3
    assert adapter.calls == 3


def test_exhausted_returns_error():
    """续写与重发都救不回 → 显式错误(不是空成功)。

    调用预算:每轮 1 次主调用 + 2 次续写,共 2 轮 = 6 次。其中 4 次是极短输入
    的续写调用,真金白银仍只花在 2 次主调用上(与旧行为等价)。
    """

    class _AlwaysBadAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            return '{"issues": ['

    from app.engines.common import ask_llm_json

    adapter = _AlwaysBadAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "p", label="测试"))
    assert err and "JSON 解析失败" in err
    assert data == {}
    assert adapter.calls == 6


def test_llm_call_exception_propagates():
    """LLM 主调用异常原样上抛,不吞成解析错误(调用方有独立的调用失败分支)。"""

    class _BoomAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            raise RuntimeError("网络超时")

    from app.engines.common import ask_llm_json

    try:
        asyncio.run(ask_llm_json(_BoomAdapter(), "p", label="测试"))
    except RuntimeError:
        pass  # 预期路径
    else:
        raise AssertionError("LLM 调用异常应原样上抛")


def test_output_without_json_skips_continue():
    """输出连 JSON 痕迹都没有 → 不浪费续写轮次,直接整篇重发。"""

    class _EmptyThenGoodAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            if self.calls == 1:
                return "抱歉,我无法完成该请求。"
            return '{"ok": true}'

    from app.engines.common import ask_llm_json

    adapter = _EmptyThenGoodAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "p", label="测试"))
    assert err is None and data == {"ok": True}
    assert adapter.calls == 2


# ---------- 关键链路集成:一致性检查被续写救回 ----------

def test_check_chapter_saved_by_continue():
    """一致性检查:截断 → 续写补全 → 正常结果,不进降级隔离。"""

    class _TruncatedThenContinueAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            if _is_continue_prompt(prompt):
                return (
                    ' "minor", "type": "state", "description": "陆辰的伤未交代来源",'
                    ' "evidence": "伤", "conflicting_fact": "", "suggestion": "补一句来源"}]}'
                )
            return '{"issues": [{"severity":'

    from test_continuity_gate import _make_db
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    adapter = _TruncatedThenContinueAdapter()
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert not is_degraded(issues), f"续写成功后不应降级,实际={issues}"
    assert adapter.calls == 2
    assert len(issues) == 1 and issues[0]["description"].startswith("陆辰")


def test_check_chapter_still_degrades_after_all_attempts_fail():
    """一致性检查:续写与重发都截 → 仍显式降级(保险不是放行后门)。"""

    class _AlwaysTruncatedAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return '{"issues": ['

    from test_continuity_gate import _make_db
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    with patch.object(checker_mod, "get_adapter_for", return_value=_AlwaysTruncatedAdapter()):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert is_degraded(issues), "全部尝试失败必须显式降级,不能当干净放行"
    assert len(issues) == 1


def test_continue_prompt_is_task_agnostic():
    """续写提示词不带任务原文:省输入 token,也不让模型跑题重写一遍。

    这是续写比整篇重发便宜的关键——重发要把上万字的原文重传一遍。
    """
    from app.engines.common import _continuation_prompt

    prompt = _continuation_prompt('{"issues": [{"severity":')
    assert _CONTINUE_MARK in prompt
    assert '{"issues": [{"severity":' in prompt  # 半截内容必须回传
    assert len(prompt) < 1200  # 只有指令 + 尾部 800 字,不含任务原文
