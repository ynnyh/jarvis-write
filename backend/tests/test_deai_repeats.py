# tests/test_deai_repeats.py
# -*- coding: utf-8 -*-
"""身体反应复读的段落级定点修复(v0.58)。

守四件事:没有重复时**零成本短路**(不调 LLM);有重复时只重写含重复的那几段;
模型输出段数对不上 / 没改好 → 一律丢弃,绝不落一版更差的正文(与 deai_self_heal 同款安全阀)。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


REPEATED = (
    "雪落了一夜,院子里积到脚踝。\n\n"
    "他攥紧了拳头,指甲渗进肉里,血珠顺着指节往下淌。\n\n"
    "他又捏住拳头,指甲渗入肉里,掌心一片湿。\n\n"
    "天亮时,他把刀收进鞘里,推门走了。"
)
CLEANED = (
    "雪落了一夜,院子里积到脚踝。\n\n"
    "他把手插进袖子里,指节在布料下绷得发白,一句话也没说。\n\n"
    "过了半炷香,他松开手,掌心那点湿意被风吹干。\n\n"
    "天亮时,他把刀收进鞘里,推门走了。"
)


class _SegAdapter:
    """返回「按顺序给出的 N 段」的最小适配器。"""

    def __init__(self, paragraphs: list[str]):
        self._paragraphs = paragraphs
        self.calls = 0

    async def ask(self, prompt, system=None):
        self.calls += 1
        return json.dumps({"paragraphs": self._paragraphs}, ensure_ascii=False)


def _heal(text: str):
    from app.engines.polish.polisher import deai_heal_repeats

    return asyncio.run(deai_heal_repeats(text))


def test_no_repeat_short_circuits_without_llm(client):
    """没有多余命中 → 原样返回且**不调 LLM**(零成本)。"""
    clean = "他把手插进袖子里,指节绷得发白。\n\n雪停了,他推门走了。"

    def _boom(*_a, **_k):
        raise AssertionError("没有重复时不该调 LLM")

    with patch("app.engines.polish.polisher.get_adapter_for", _boom):
        out, before, after = _heal(clean)
    assert out == clean and before.score == after.score


def test_adopts_rewrite_when_repeat_removed(client):
    """重复被换掉且分数下降 → 采纳,正文里不再有重复。"""
    from app.engines.polish.ai_flavor import body_reaction_hits

    assert body_reaction_hits(REPEATED), "测试文本本身应含多余命中"
    # 目标段 = 含多余命中的那一句所在段(第三段)
    adapter = _SegAdapter([CLEANED.split("\n\n")[2]])
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        out, before, after = _heal(REPEATED)
    assert adapter.calls == 1
    assert out != REPEATED
    assert after.score < before.score
    assert body_reaction_hits(out) == []          # 重复被治掉了
    # 第一次保留(它多半是必要的),改的是多余的那一处
    assert "攥紧" in out
    assert "他又捏住" not in out and "指甲渗入" not in out
    assert "天亮时" in out                        # 没被动的段落原样保留


def test_discards_when_paragraph_count_mismatch(client):
    """模型给的段数对不上 → 丢弃(绝不半途替换)。"""
    adapter = _SegAdapter(["只给一段", "还多给一段"])   # 期望 1 段,模型给 2 段
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        out, before, after = _heal(REPEATED)
    assert out == REPEATED and before.score == after.score


def test_discards_when_not_improved(client):
    """模型原样退回(没降分)→ 丢弃。"""
    third = REPEATED.split("\n\n")[2]
    adapter = _SegAdapter([third])
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        out, before, after = _heal(REPEATED)
    assert out == REPEATED


def test_discards_on_length_blowup(client):
    """模型把这一段写成大长篇(篇幅越界)→ 丢弃。"""
    adapter = _SegAdapter(["他攥紧拳头,指甲渗进肉里。" + "雪。" * 400])
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        out, _before, _after = _heal(REPEATED)
    assert out == REPEATED


def test_fatigue_block_carries_body_reaction_note(client):
    """生成端预防:最近几章里 ≥2 章用过同一族 → 进疲劳词表(措辞是换载体,不是禁止)。"""
    from app.engines.polish.polisher import fatigue_block

    block = fatigue_block([REPEATED, REPEATED])
    assert "身体反应该换载体" in block
    assert "攥拳掐肉/掌心渗血" in block
    note = block.split("身体反应该换载体")[1]
    assert "换一种写法" in note and "严禁" not in note   # 措辞是换载体,不是禁词
