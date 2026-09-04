# tests/test_evals_fixtures.py
# -*- coding: utf-8 -*-
"""评测底座·夹具:内置加载、validate_fixture 拒收不合格、export_project 抽要素。

不依赖网络 / 不打 LLM,只测纯数据校验与装卸。
"""
from __future__ import annotations

import pytest

from app.evals.fixtures import (
    Fixture,
    fixture_from_dict,
    list_fixtures,
    load_fixture,
    validate_fixture,
)


def test_built_in_fixtures_list_includes_po_feng_ji():
    names = list_fixtures()
    assert "po_feng_ji" in names
    fx = load_fixture("po_feng_ji")
    assert isinstance(fx, Fixture)
    assert fx.title == "破封纪"
    assert fx.chapter_count == 10
    assert fx.target_words == 2500


def test_validate_fixture_rejects_when_outline_chapter_numbers_not_sequential():
    fx = Fixture(
        name="bad",
        title="坏夹具",
        topic="测试",
        genre="测试",
        architecture={
            "core_seed": "x",
            "character_dynamics": "x",
            "world_building": "x",
            "plot_architecture": "x",
        },
        outlines=[
            {
                "chapter_number": 1,
                "title": "第一章",
                "chapter_role": "起",
                "chapter_purpose": "p",
                "suspense_level": "低",
                "foreshadowing": "",
                "plot_twist_level": "无",
                "summary": "s",
                "beats": ["b1"],
                "characters_involved": [],
                "key_items": [],
                "scene_location": "地",
            },
            {
                # 跳号:1,3——validate 必拒
                "chapter_number": 3,
                "title": "第三章",
                "chapter_role": "起",
                "chapter_purpose": "p",
                "suspense_level": "低",
                "foreshadowing": "",
                "plot_twist_level": "无",
                "summary": "s",
                "beats": ["b1"],
                "characters_involved": [],
                "key_items": [],
                "scene_location": "地",
            },
        ],
    )
    problems = validate_fixture(fx)
    assert any("chapter_number" in p for p in problems)


def test_validate_fixture_rejects_empty_architecture_field():
    fx = Fixture(
        name="bad",
        title="坏夹具2",
        topic="测试",
        genre="测试",
        architecture={
            "core_seed": "x",
            "character_dynamics": "",
            # world_building / plot_architecture 缺
            "world_building": "x",
            "plot_architecture": "",
        },
        outlines=[
            {
                "chapter_number": 1,
                "title": "第一章",
                "chapter_role": "起",
                "chapter_purpose": "p",
                "suspense_level": "低",
                "foreshadowing": "",
                "plot_twist_level": "无",
                "summary": "s",
                "beats": ["b1"],
                "characters_involved": [],
                "key_items": [],
                "scene_location": "地",
            }
        ],
    )
    problems = validate_fixture(fx)
    assert any("character_dynamics" in p for p in problems)
    assert any("plot_architecture" in p for p in problems)


def test_fixture_from_dict_drops_unknown_keys_silently():
    data = {
        "name": "x",
        "title": "x",
        "topic": "x",
        "genre": "x",
        "architecture": {
            "core_seed": "a",
            "character_dynamics": "a",
            "world_building": "a",
            "plot_architecture": "a",
        },
        "outlines": [
            {
                "chapter_number": 1,
                "title": "t",
                "chapter_role": "r",
                "chapter_purpose": "p",
                "suspense_level": "低",
                "foreshadowing": "",
                "plot_twist_level": "无",
                "summary": "s",
                "beats": ["b"],
                "characters_involved": [],
                "key_items": [],
                "scene_location": "sc",
            }
        ],
        "target_words": 2500,
        "should_be_ignored": 999,
    }
    fx = fixture_from_dict(data)
    assert fx.name == "x"
    assert not hasattr(fx, "should_be_ignored")


def test_load_fixture_raises_for_unknown_name():
    with pytest.raises(FileNotFoundError):
        load_fixture("nonexistent_fixture_xyz")
