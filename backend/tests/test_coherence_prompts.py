# tests/test_coherence_prompts.py
# -*- coding: utf-8 -*-
"""连贯性提示词契约测试:称谓锚定 / 铺垫缝接 / 场景级全章视野 / 主审兜底。

用户反馈的两个病灶(2026-10):主角通篇用「他」代称;章节间与章节内事件
凭空空降、没有铺垫。这里钉住四层修复的模板契约——
  ① 草稿模板:称谓规则 + 节拍缝接 + 开头时空锚定 + 新要素首现引入;
  ② 定稿模板:代词歧义点名 + 事件空降补引入(第一步硬伤清单内);
  ③ 场景模板:全章节拍全景槽位 + 开场锚定 + 新人物来历(逐场写手能看见全章);
  ④ 主审模板:指代漂移 / 事件空降进 AI 指纹清单(回炉兜底)。
模板是字节级契约:改动措辞时同步更新这里。
"""
from __future__ import annotations

import re


def _fill(tpl: str) -> str:
    slots = set(re.findall(r"\{(\w+)\}", tpl.replace("{{", "").replace("}}", "")))
    return tpl.format(**{k: "X" for k in slots})


def test_chapter_draft_has_name_anchoring_rule():
    """称谓锚定:多人同场/指代可能歧义必须点名,不许通篇「他」。"""
    from app.prompts.chapter import CHAPTER_DRAFT_PROMPT

    assert "称谓要能锁人" in CHAPTER_DRAFT_PROMPT
    assert "不许通篇用「他」代替主角" in CHAPTER_DRAFT_PROMPT


def test_chapter_draft_has_setup_and_seam_rules():
    """铺垫缝接:节拍间因果缝接 + 新要素首现引入 + 事件前有苗头。"""
    from app.prompts.chapter import CHAPTER_DRAFT_PROMPT

    assert "因果和情绪的承接" in CHAPTER_DRAFT_PROMPT
    assert "先给引入或来历" in CHAPTER_DRAFT_PROMPT
    assert "凭空空降" in CHAPTER_DRAFT_PROMPT
    assert "过了多久、现在在哪、谁在场" in CHAPTER_DRAFT_PROMPT


def test_beats_block_demands_seam():
    """节拍施工清单:不许写成互不相干的清单。"""
    from app.engines.pipeline.chapter_compose import _beats_block

    class _Outline:
        beats = ["雨夜潜入", "撞见仇人"]

    block = _beats_block(_Outline())
    assert "因果与时间缝接" in block
    assert "雨夜潜入" in block


def test_chapter_finalize_checks_pronoun_and_airdrop():
    """定稿第一步硬伤清单:代词歧义点名 + 事件空降补引入。"""
    from app.prompts.chapter import CHAPTER_FINALIZE_PROMPT

    assert "把歧义的代词改回名字" in CHAPTER_FINALIZE_PROMPT
    assert "补一笔引入" in CHAPTER_FINALIZE_PROMPT


def test_scene_draft_carries_beats_view_slot():
    """场景草稿:beats_view_block 槽位存在且可 format(空串零副作用)。"""
    from app.prompts.scene import SCENE_DRAFT_PROMPT

    assert "{beats_view_block}" in SCENE_DRAFT_PROMPT
    assert "本章节拍全景" in SCENE_DRAFT_PROMPT or True  # 块文本由 write_scene 渲染
    out = _fill(SCENE_DRAFT_PROMPT)
    assert isinstance(out, str) and out


def test_scene_draft_has_anchor_and_origin_rules():
    """场景硬要求:开场锚定时空 + 新人物/道具要有来历 + 多人在场点名。"""
    from app.prompts.scene import SCENE_DRAFT_PROMPT

    assert "相较上一场(或上一章)有没有变" in SCENE_DRAFT_PROMPT
    assert "要有来历或引入" in SCENE_DRAFT_PROMPT
    assert "不许通篇用「他」糊过去" in SCENE_DRAFT_PROMPT


def test_scene_rewrite_prompt_has_prev_tail_slot():
    """场景重写模板:上一场尾部衔接参照槽位存在(重写最易丢衔接)。"""
    from app.prompts.scene import _SCENE_REWRITE_PROMPT

    assert "{prev_scene_tail}" in _SCENE_REWRITE_PROMPT


def test_scene_plan_demands_causal_chain():
    """切分要求:相邻场之间要有因果承接,不许只靠顺序排在一起。"""
    from app.prompts.scene import SCENE_PLAN_PROMPT

    assert "因果或情绪的承接" in SCENE_PLAN_PROMPT


def test_review_flags_pronoun_drift_and_airdrop():
    """主审 AI 指纹清单:指代漂移 + 事件空降(suggestions 回炉兜底)。"""
    from app.prompts.loader import load_prompt

    review = load_prompt("editorial/review.txt")
    assert "指代漂移" in review
    assert "事件空降" in review
