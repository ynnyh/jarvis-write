# tests/test_fact_batching.py
# -*- coding: utf-8 -*-
"""降爆:超长事实清单自动分批。

为什么需要(2026-09-08 50 章压测):长篇写到后期,圣经事实累积到几百条,
一次全喂会让请求体量与模型输出一起膨胀,而这正是中转网关掐连接的温床——
输出越长越容易停在半途(魔芋实测连 16 字符的 JSON 都截)。超过阈值就分批改查。

纪律:
- 阈值取 6000 字符,黄金样本远不到这条线,**正常路径仍是 1 批、行为不变**
  (评测基线不受影响);
- 按整行切,一条事实绝不被拦腰截断;
- 批次上限用尽时剩余的全部塞进最后一批——宁可这批偏长,也不悄悄丢事实漏检。
"""
from __future__ import annotations

from app.engines.consistency.checker import (
    _MAX_FACT_BATCHES,
    _dedup,
    _split_fact_block,
)


def _make_facts(n: int, width: int = 60) -> str:
    return "\n".join(f"- 第{i}号事实:{'内容' * width}(章{i})" for i in range(n))


def test_short_block_stays_single_batch():
    """短清单 → 1 批,且原样返回(连换行格式都不许变)。"""
    block = "- 事实甲:陆辰失聪\n- 事实乙:玉佩已碎"
    assert _split_fact_block(block) == [block]


def test_placeholder_text_is_never_split():
    """「(暂无…)」占位文案不是事实清单,不能参与分批。"""
    block = "(故事圣经暂无有效事实,本次仅对照上一章契约与结尾原文)"
    assert _split_fact_block(block) == [block]


def test_long_block_is_split_without_losing_facts():
    """超长清单分批后:批次不超上限,且一条事实都不许丢。"""
    lines = _make_facts(120).splitlines()
    batches = _split_fact_block("\n".join(lines))

    assert 1 < len(batches) <= _MAX_FACT_BATCHES
    merged = [line for batch in batches for line in batch.splitlines() if line.strip()]
    assert merged == lines, "分批只能改变分组,不能改变内容"


def test_no_fact_is_torn_in_half():
    """按整行切:批边界处不会残留被截断的半条事实。"""
    batches = _split_fact_block(_make_facts(80))
    for batch in batches:
        for line in batch.splitlines():
            assert line.startswith("- 第"), f"出现非完整行:{line[:30]}"


def test_batch_cap_keeps_remaining_in_last_batch():
    """批次达上限后剩余的全部进最后一批:不丢事实优先于批长度均衡。"""
    lines = _make_facts(300).splitlines()
    batches = _split_fact_block("\n".join(lines))

    assert len(batches) == _MAX_FACT_BATCHES
    assert sum(len(b.splitlines()) for b in batches) == len(lines)


def test_dedup_merges_cross_batch_repeats():
    """分批会让同一矛盾在不同批里重复出现,合并后必须去重(否则 blocker 虚高)。"""
    issues = [
        {"description": "陆辰的伤没交代来源", "evidence": "肩上旧伤"},
        {"description": "陆辰的伤没交代来源", "evidence": "肩上旧伤"},
        {"description": "玉佩不该出现在这里", "evidence": "桌上的玉佩"},
    ]
    assert len(_dedup(issues)) == 2


def test_dedup_keeps_same_description_different_evidence():
    """描述相同但证据不同是两处不同的矛盾,去重不能误杀。"""
    issues = [
        {"description": "时间线冲突", "evidence": "第三章说是清晨"},
        {"description": "时间线冲突", "evidence": "第七章说是黄昏"},
    ]
    assert len(_dedup(issues)) == 2
