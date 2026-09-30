# tests/test_line_skill_injection.py
# -*- coding: utf-8 -*-
"""出片线 Skill 包接入(docs/25):按节点命中 / 跨线复用 / 包控开关的零回归。

这一组是本轮最要紧的回归网,钉三件事:
1. 六个新包真的能被 active_packs 按 scope+node 命中(挂上就生效,不是摆设);
2. 小说爽文包能跨线命中漫剧线(docs/25 拍板 1——省掉重写一遍漫剧剧本工艺);
3. **关掉包时输出逐字回落**——这是全部改造的零回归承诺,必须能测出来。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.models import SkillPack
from app.db.session import SessionLocal
from app.engines.skills import packs as skill_packs
from app.main import app


@pytest.fixture(scope="module")
def _started():
    """起一次 app 走完 lifespan——建表/迁移在那里,不在 import 期。

    本组直接用 SessionLocal 而不引 client,但仍需借一次启动把 schema 建出来,
    否则第一句查询就是 no such table: skill_packs。
    """
    with TestClient(app):
        yield


@pytest.fixture
def restore_enabled(_started):
    """测试改完 pack.enabled 后按**官方默认值**还原。

    为什么必须:整个 tests/ 共用 conftest 里那一个临时库,本组的改动会被后面
    跑到的 test_skill_packs 看见(它断言官方包的默认开关),表现为「单跑绿、
    合跑红」——最难查的那种红。

    为什么按 BUILTIN_PACKS 还原而不是「进测试时快照、出测试时回填」:
    快照法在「快照那一刻状态已经脏了」时会忠实地把脏状态还原回去,
    而测试执行顺序会变,那就变成偶发红。按 spec 还原与顺序无关。
    """
    yield
    defaults = {s["pack_key"]: bool(s.get("enabled", True)) for s in skill_packs.BUILTIN_PACKS}
    with SessionLocal() as db:
        for row in db.query(SkillPack).all():
            if row.pack_key in defaults and row.enabled != defaults[row.pack_key]:
                row.enabled = defaults[row.pack_key]
        db.commit()


def _pack(db, key: str) -> SkillPack:
    skill_packs.ensure_builtin_packs(db)
    row = db.query(SkillPack).filter(SkillPack.pack_key == key).one()
    return row


def _hits(db, scope: str, node: str) -> list[str]:
    return [p.pack_key for p in skill_packs.active_packs(db, scope=scope, node=node)]


# ==================== ① 包按节点命中 ====================

def test_drama_scene_pack_hits_draft_when_enabled(restore_enabled):
    """场次爽点包默认关(内容口径类);开启后应命中 draft 节点。"""
    with SessionLocal() as db:
        assert "drama-scene-punch" not in _hits(db, "drama", "draft")
        _pack(db, "drama-scene-punch").enabled = True
        db.commit()
        assert "drama-scene-punch" in _hits(db, "drama", "draft")


def test_storyboard_pack_is_shared_by_three_lines(restore_enabled):
    """分镜功底包三线共用一份(docs/25 §2.1:同义包合并 scope,不各写改版)。"""
    with SessionLocal() as db:
        for scope in ("anime", "drama", "promo"):
            assert "storyboard-basics" in _hits(db, scope, "shots"), scope
        # 反面:它只挂在 shots 节点,不该漏进 draft/render
        assert "storyboard-basics" not in _hits(db, "drama", "draft")
        assert "storyboard-basics" not in _hits(db, "drama", "render")


def test_line_packs_do_not_leak_across_lines(restore_enabled):
    """出片线的包不许串线——promo 的包不该出现在 drama 的注入里。"""
    with SessionLocal() as db:
        for key in ("drama-shotcard-render", "promo-hook-3s",
                    "promo-landmark-guard", "clips-emotion-curve"):
            _pack(db, key).enabled = True
        db.commit()
        assert "promo-hook-3s" in _hits(db, "promo", "outline")
        assert "promo-hook-3s" not in _hits(db, "drama", "outline")
        assert "clips-emotion-curve" not in _hits(db, "drama", "draft")
        assert "drama-shotcard-render" not in _hits(db, "promo", "render")


# ==================== ② 跨线复用(拍板 1) ====================

def test_novel_shuangwen_pack_reaches_drama_via_mount(restore_enabled):
    """漫剧线复用小说爽文包:靠书级 mounted_packs 挂载,不靠全局开关。

    这条钉的是 docs/25 的核心收益——爽文包 v2(双爽点/打脸三件套/文体示范)
    一行内容都不用重写,漫剧剧本工序直接吃到。
    """
    with SessionLocal() as db:
        assert "drama_source_male" not in _hits(db, "drama", "draft")  # 默认关
        mounted = skill_packs.active_packs(db, scope="drama", node="draft",
                                           mounted_keys=["drama_source_male"])
        assert "drama_source_male" in [p.pack_key for p in mounted]
        # 同一包在小说线也要靠挂载才生效——全局关就是全局关
        assert ("drama_source_male" in _hits(db, "novel", "draft")) is False
        assert any(p.pack_key == "drama_source_male" for p in skill_packs.active_packs(
            db, scope="novel", node="draft", mounted_keys=["drama_source_male"]))


def test_mounted_packs_still_limited_by_scope(restore_enabled):
    """挂载清单不能让包跨线生效——男频包挂到 clips 上必须无效。"""
    with SessionLocal() as db:
        got = [p.pack_key for p in skill_packs.active_packs(
            db, scope="clips", node="draft", mounted_keys=["drama_source_male"])]
        assert "drama_source_male" not in got


# ==================== ③ 包控开关的零回归(最重要) ====================

def test_clips_length_rule_toggles_and_falls_back_verbatim(restore_enabled):
    """紧凑封顶包:开=封顶口径,关=**逐字**回落旧口径。

    「逐字」是这条断言的全部意义:它保证默认关闭的用户和没做这次改造之前
    拿到的是同一份提示词(甲方案的核心承诺)。
    """
    from app.engines.clips.film_prompt import _length_rule

    with SessionLocal() as db:
        _pack(db, skill_packs.CLIPS_COMPACT_PACK_KEY).enabled = False
        db.commit()
        legacy = _length_rule(db, 15, "400 字")

        _pack(db, skill_packs.CLIPS_COMPACT_PACK_KEY).enabled = True
        db.commit()
        compact = _length_rule(db, 15, "400 字")

    assert "不少于 400 字" in legacy
    assert "上不封顶" in legacy
    assert "不超过 400 字" in compact
    assert "紧凑封顶" in compact
    assert "必写五项缺一不可" in compact
    assert legacy != compact
    # 两条都带全片时长与字数指引,别把变量填丢
    assert "15" in legacy and "15" in compact


def test_clips_compact_pack_is_enabled_by_default(restore_enabled):
    """作者 2026-09-28 拍板:紧凑封顶包默认开(旧口径已被实测证伪且与台词口径打架)。"""
    with SessionLocal() as db:
        assert _pack(db, skill_packs.CLIPS_COMPACT_PACK_KEY).enabled is True


def test_whole_clip_template_has_length_rule_slot(restore_enabled):
    """模板里必须是 {length_rule} 占位,而不是写死的旧口径。

    写成硬文本的话,包开关就是个摆设——这是「槽加了没人填」的镜像失效。
    """
    from app.prompts.film_prompt import WHOLE_CLIP_PROMPT_TEMPLATE

    assert "{length_rule}" in WHOLE_CLIP_PROMPT_TEMPLATE
    assert "上不封顶" not in WHOLE_CLIP_PROMPT_TEMPLATE.split("LENGTH_RULE_LEGACY")[0]
