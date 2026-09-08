# tests/test_json_retry.py
# -*- coding: utf-8 -*-
"""LLM JSON 任务重试保险:解析失败自动原样重打一次。

背景(2026-09-08 50 章压测):中转渠道会把响应尾巴随机截断——连
`{"issues": []`(16 字符)都被截在半途,导致 9/13 章一致性检查/主审
解析失败而进隔离。截断是随机的,重打一次大概率就好。

本文件锁死行为:
1. 首次解析失败 → 重试一次;重试成功 → 正常返回(且只调 2 次);
2. 重试仍失败 → 显式错误交回调用方走降级(不吞错、不冒充干净);
3. 首次就成功 → 绝不多打(不浪费 token);
4. LLM 调用本身的异常原样上抛(与解析失败是两种现场)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

# ---------- ask_llm_json 单元行为 ----------

def test_retry_recovers_from_truncated_json():
    """首次截断、重试成功 → 返回数据,恰好调用 2 次。"""

    class _FlakyAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            if self.calls == 1:
                return '{"issues": ['  # 模拟中转截断
            return '{"issues": []}'

    from app.engines.common import ask_llm_json

    adapter = _FlakyAdapter()
    data, err = asyncio.run(ask_llm_json(adapter, "p", label="测试"))
    assert err is None
    assert data == {"issues": []}
    assert adapter.calls == 2


def test_retry_exhausted_returns_error():
    """重试仍失败 → 返回显式错误(不是空成功),恰好调用 2 次后停。"""

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
    assert adapter.calls == 2


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


def test_llm_call_exception_propagates():
    """LLM 调用异常原样上抛,不吞成解析错误(调用方有独立的调用失败分支)。"""

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


# ---------- 关键链路集成:一致性检查被重试救回 ----------

def test_check_chapter_saved_by_retry():
    """一致性检查:首次截断 + 重试成功 → 正常结果,不进降级隔离。"""

    class _TruncatedThenGoodAdapter:
        def __init__(self):
            self.calls = 0

        async def ask(self, prompt: str, system=None) -> str:
            self.calls += 1
            if self.calls == 1:
                return '{"issues": [{"severity": "minor"'  # 截断
            return '{"issues": [{"severity": "minor", "type": "state", "description": "陆辰的伤未交代来源", "evidence": "伤", "conflicting_fact": "", "suggestion": "补一句来源"}]}'

    from test_continuity_gate import _make_db
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    adapter = _TruncatedThenGoodAdapter()
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert not is_degraded(issues), f"重试成功后不应降级,实际={issues}"
    assert adapter.calls == 2
    assert len(issues) == 1 and issues[0]["description"].startswith("陆辰")


def test_check_chapter_still_degrades_after_retry_fails():
    """一致性检查:重试仍截断 → 仍显式降级(保险不是放行后门)。"""

    class _AlwaysTruncatedAdapter:
        async def ask(self, prompt: str, system=None) -> str:
            return '{"issues": ['

    from test_continuity_gate import _make_db
    from app.engines.common import is_degraded
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    with patch.object(checker_mod, "get_adapter_for", return_value=_AlwaysTruncatedAdapter()):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, "第二章正文。" * 30))

    assert is_degraded(issues), "重试仍失败必须显式降级,不能当干净放行"
    assert len(issues) == 1
