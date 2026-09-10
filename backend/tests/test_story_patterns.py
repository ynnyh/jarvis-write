# tests/test_story_patterns.py
"""故事骨架胶囊(dna_capsules 的情节组织对应物)。

锁四个语义:
- choices 不泄露 opener 正文(前端选择即可,不必先读示范);
- render_pattern_block:选中时给「结构配方+节奏+桥段+开场示范」整块,
  未选/未知 key → 空串(prompt 一字不变,评测基线不受影响);
- pattern_key 进 StoryDNA:is_empty 口径、coerce_dna 收敛;
- dna_block_of 末尾追加骨架块(DNA 全链路注入的载体)。
"""
from app.engines.tendency.assembler import dna_block_of
from app.prompts.story_patterns import (
    STORY_PATTERNS,
    get_story_pattern,
    pattern_choices,
    render_pattern_block,
)
from app.schemas.dna import StoryDNA, coerce_dna


def test_choices_shape_no_opener_leak():
    choices = pattern_choices()
    assert len(choices) == len(STORY_PATTERNS)
    for ch in choices:
        assert set(ch) == {"key", "name", "comps_hint", "formula", "rhythm"}
    # 用户的两个例子都有对应骨架:阶层碰撞(练习生×财阀)与前世今生
    keys = {c["key"] for c in choices}
    assert {"class_gap_romance", "past_life", "face_slap"} <= keys


def test_render_block_selected_and_empty():
    block = render_pattern_block("face_slap")
    assert "故事骨架" in block
    assert "骨架配方" in block and "节奏规则" in block
    assert "开场示范" in block  # few-shot 正例
    # 空串语义:未选/未知 → prompt 该块整体省略
    assert render_pattern_block("") == ""
    assert render_pattern_block("no_such_key") == ""


def test_opener_is_original_and_multiline():
    p = get_story_pattern("rebirth_revenge")
    assert p is not None and p.opener
    assert "\n" in p.opener  # 多行短场景
    assert len(p.opener) < 300  # 示范只学结构,不堆篇幅


def test_dna_pattern_key_semantics():
    # 只选骨架也算表了态
    d = StoryDNA(pattern_key="past_life")
    assert not d.is_empty()
    assert StoryDNA().is_empty()
    # coerce_dna 收敛:脏输入只留已知 key
    got = coerce_dna({"pattern_key": " face_slap ", "taste_key": "", "junk": 1})
    assert got.pattern_key == "face_slap" and "junk" not in got.model_dump()
    assert coerce_dna(None).pattern_key == ""


def test_dna_block_includes_pattern():
    dna = StoryDNA(pattern_key="flash_marriage")
    block = dna_block_of(dna)
    assert "故事骨架" in block and "契约" in block
    # 无 DNA → 空串(现有书行为不变)
    assert dna_block_of(None) == ""
    assert dna_block_of(StoryDNA()) == ""


def test_dna_block_of_dict_payload():
    # project.dna 落库是 JSON dict,coerce 路径必须通
    block = dna_block_of({"pattern_key": "underdog"})
    assert "骨架配方" in block and "退婚书摔在陈默脸上" in block


def test_dna_options_endpoint_returns_patterns():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/auth/register",
            json={"username": "pattern_probe", "password": "pass123", "invite_code": "test-invite"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"] if "token" in r.json() else r.json().get("access_token")
        body = client.get(
            "/api/inspire/dna/options", headers={"Authorization": f"Bearer {token}"}
        ).json()
    assert isinstance(body["patterns"], list) and body["patterns"]
    assert set(body["patterns"][0]) == {"key", "name", "comps_hint", "formula", "rhythm"}

