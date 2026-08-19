# tests/test_style_capsules.py
# -*- coding: utf-8 -*-
"""风格胶囊 & 配对反例:去 AI 味的「正向锚定」素材(纯函数,无 LLM)。"""
from app.prompts.style_capsules import (
    CAPSULES,
    capsule_choices,
    get_capsule,
    pairwise_examples_block,
    render_voice_block,
)


def test_capsules_have_unique_keys_and_required_fields():
    assert len(CAPSULES) >= 6
    keys = [c.key for c in CAPSULES]
    assert len(keys) == len(set(keys)), "胶囊 key 必须唯一"
    for c in CAPSULES:
        assert c.key and c.name and c.directive and c.sample
        assert len(c.sample) >= 10, f"{c.key} 的仿写示范太短"


def test_get_capsule_known_and_unknown():
    assert get_capsule("yuhua") is not None
    assert get_capsule("yuhua").name  # 有展示名
    assert get_capsule("") is None
    assert get_capsule("does-not-exist") is None
    assert get_capsule("  yuhua  ") is not None  # 容忍首尾空白


def test_capsule_choices_shape_no_sample_leak():
    choices = capsule_choices()
    assert len(choices) == len(CAPSULES)
    for ch in choices:
        assert set(ch) == {"key", "name", "directive"}  # 不泄露 sample 正文
    assert [ch["key"] for ch in choices] == [c.key for c in CAPSULES]  # 顺序一致


def test_render_voice_block_both_empty_returns_blank():
    assert render_voice_block("", "") == ""
    assert render_voice_block() == ""


def test_render_voice_block_with_capsule_marks_copyright():
    block = render_voice_block(voice_key="yuhua")
    assert "文风范本" in block
    cap = get_capsule("yuhua")
    assert cap.name in block
    assert cap.directive in block
    # 版权红线:名家笔法必须标注「风格参考·非原作节选」
    assert "风格参考" in block and "非原作节选" in block


def test_render_voice_block_user_sample_truncated():
    block = render_voice_block(voice_sample="字" * 5000)
    assert "作者自备" in block
    assert block.count("字") <= 1200  # 范本注入截断到上限


def test_render_voice_block_capsule_and_sample_combined():
    block = render_voice_block(voice_key="luxun", voice_sample="他不说话。")
    assert get_capsule("luxun").name in block
    assert "他不说话。" in block


def test_pairwise_examples_block():
    block = pairwise_examples_block()
    assert "AI 腔" in block and "人话" in block
    assert "✗" in block and "✓" in block
    # 有多对,且 ✗/✓ 成对出现
    assert block.count("✗") == block.count("✓")
    assert block.count("✗") >= 5


def test_voice_block_of_from_profile_and_empty_cases():
    """voice_block_of:从 global_tendency 的创作档案取 voice,渲染成注入块;
    无档案 / 非法结构一律返回空串(各正文入口据此决定是否追加正向锚)。"""
    from app.engines.tendency.assembler import voice_block_of

    # 选了名家/预设胶囊 → 渲染出对应范本(含版权标注)
    block = voice_block_of({"_profile": {"voice_key": "plain"}})
    assert "文风范本" in block
    assert get_capsule("plain").name in block
    # 作者自备范文单独也能出块
    assert "他不说话。" in voice_block_of({"_profile": {"voice_sample": "他不说话。"}})
    # 空 / 缺档案 / 空字段 / 非法结构 → 空串
    assert voice_block_of(None) == ""
    assert voice_block_of({}) == ""
    assert voice_block_of({"_profile": {}}) == ""
    assert voice_block_of({"_profile": {"voice_key": "", "voice_sample": ""}}) == ""
    assert voice_block_of({"_profile": "不是字典"}) == ""
