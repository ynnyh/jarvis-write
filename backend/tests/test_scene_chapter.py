# tests/test_scene_chapter.py
# -*- coding: utf-8 -*-
"""场景级章节编排测试(mock LLM):逐场生成 → 逐场验收 → 只重写不合格场 → 拼接。

钉住的核心契约:
1. 开关关闭时完全走老路径(一次调用写整章),行为零变化;
2. 开关打开时按场次调用,调用次数 = 场数 × (1 + 重写次数);
3. 验收未过的场只重写它自己,已通过的场不再动;
4. 重写封顶 2 次后接受当前版本,不再烧钱;
5. 单场生成抛异常不拖垮整章,已生成的场保留;
6. anchors 与拼接后的正文严格对应(场景级定稿/定点修靠它定位)。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.engines.pipeline import scene_chapter as sc


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class _Adapter:
    """按调用顺序返回预置回复;回复可以是字符串或异常。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[str] = []

    async def ask(self, prompt, system=None):
        self.calls.append(prompt)
        if not self._replies:
            return "默认正文。"
        nxt = self._replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


_SCENE_JSON = json.dumps(
    [
        {"title": "雨夜潜入", "summary": "翻墙潜入", "location": "后山",
         "characters": ["林晚"], "goal": "摸清位置", "conflict": "守卫不明",
         "emotion_target": "紧绷", "tension_level": 1, "target_words": 1000,
         "fact_hints": []},
        {"title": "祭坛撞破", "summary": "撞见仇人", "location": "祭坛",
         "characters": ["林晚", "周衍"], "goal": "确认身份", "conflict": "不能动手",
         "emotion_target": "灼痛", "tension_level": 5, "target_words": 1500,
         "fact_hints": []},
        {"title": "强行脱身", "summary": "炸坛遁走", "location": "后山",
         "characters": ["林晚"], "goal": "活着离开", "conflict": "追兵不止",
         "emotion_target": "冷硬", "tension_level": 3, "target_words": 1200,
         "fact_hints": []},
    ],
    ensure_ascii=False,
)


def _verdict(emotion=9, goal=9, concreteness=9, comment="好"):
    return json.dumps({
        "scores": {"emotion_fit": emotion, "goal_done": goal, "tension_fit": 8,
                   "concreteness": concreteness, "prose": 8},
        "comment": comment, "suggestions": [],
    }, ensure_ascii=False)


def _fail_verdict(dim="emotion_fit", comment="情绪没到位"):
    s = {"emotion_fit": 9, "goal_done": 9, "tension_fit": 8,
         "concreteness": 9, "prose": 8}
    s[dim] = 3
    return json.dumps({"scores": s, "comment": comment,
                       "suggestions": [{"evidence": "他很紧张", "issue": "直陈",
                                        "fix": "改成手抖"}]}, ensure_ascii=False)


def _setup(monkeypatch, replies):
    """建库 + 项目 + 蓝图,并把 scene_plan / scene_write 的适配器都指向同一个 mock。

    用同一个 mock 是因为两条链路都走 get_adapter_for,而路由按 Task 分派;
    测试里我们关心的是「调用顺序与次数」,不关心档位。
    """
    from app.db.models import Outline, Project

    db = _db()
    project = Project(
        title="破封纪", topic="修仙", genre="仙侠",
        target_chapters=10, target_words_per_chapter=3000,
        scene_level_enabled=True,
    )
    db.add(project)
    db.commit()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="夜行",
        chapter_role="高潮", chapter_purpose="第一次正面出手",
        emotional_tone="紧绷", suspense_level="高", scene_anchor="他终于认出那道疤",
        summary="夜探敌营,撞见仇人。",
        beats=["雨夜潜入", "撞见仇人", "强行脱身"],
        characters_involved=["林晚"], scene_location="敌营后山",
    ))
    db.commit()

    adapter = _Adapter(replies)
    # scene_chapter 本身不直接取适配器,它委托给 scene_plan / scene_write,
    # 所以只需把这两个模块的 get_adapter_for 指向同一个 mock。
    import app.engines.pipeline.scene_plan as sp_mod
    import app.engines.pipeline.scene_write as sw_mod

    monkeypatch.setattr(sp_mod, "get_adapter_for", lambda task: adapter)
    monkeypatch.setattr(sw_mod, "get_adapter_for", lambda task: adapter)
    return db, project, adapter


def _compose(db, project, **kw):
    params = dict(
        style_block="【文风】", deai_rules="【禁令】",
        rolling_summary="", recent_tail="", handoff_block="",
        outline_summary="夜探敌营。", outline_title="夜行",
        scene_anchor="他终于认出那道疤", threshold=7,
    )
    params.update(kw)
    return asyncio.run(sc.compose_by_scenes(db, project, 1, **params))


# ---------- 正常路径 ----------

def test_all_scenes_pass(monkeypatch):
    """3 场全通过:1 次切分 + 3 次生成 + 3 次验收 = 7 次调用。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场正文。", _verdict(), "第二场正文。", _verdict(),
         "第三场正文。", _verdict()],
    )
    result = _compose(db, project)

    assert result.stats["scene_count"] == 3
    assert result.stats["accepted"] == 3
    assert result.stats["rejected"] == 0
    assert result.stats["rewrites"] == 0
    assert len(adapter.calls) == 7


def test_text_is_joined_scene_bodies(monkeypatch):
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场。", _verdict(), "第二场。", _verdict(),
         "第三场。", _verdict()],
    )
    result = _compose(db, project)
    assert "第一场。" in result.text
    assert "第二场。" in result.text
    assert "第三场。" in result.text
    # 顺序保持
    assert result.text.index("第一场。") < result.text.index("第二场。")
    assert result.text.index("第二场。") < result.text.index("第三场。")


def test_anchors_match_text(monkeypatch):
    """anchors 必须能被正文切片命中(场景级定稿靠它定位)。"""
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场正文内容。", _verdict(), "第二场正文内容。", _verdict(),
         "第三场正文内容。", _verdict()],
    )
    result = _compose(db, project)

    assert len(result.anchors) == 3
    for (start, end), scene in zip(result.anchors, result.scenes):
        assert result.text[start:end] == (scene.content or "").strip()


def test_scene_status_and_anchors_persisted(monkeypatch):
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "甲", _verdict(), "乙", _verdict(), "丙", _verdict()],
    )
    result = _compose(db, project)
    for scene in result.scenes:
        assert scene.status == "accepted"
        assert scene.word_count > 0
        assert scene.anchor_end > scene.anchor_start


# ---------- 逐场验收与定点重写 ----------

def test_only_failing_scene_is_rewritten(monkeypatch):
    """第 2 场未过 → 只重写第 2 场,第 1/3 场不再调用。"""
    db, project, adapter = _setup(
        monkeypatch,
        [
            _SCENE_JSON,
            "第一场。", _verdict(),
            "第二场初版。", _fail_verdict(),
            "第二场重写版。", _verdict(),
            "第三场。", _verdict(),
        ],
    )
    result = _compose(db, project)

    assert result.stats["rewrites"] == 1
    assert result.stats["rejected"] == 0
    assert "第二场重写版。" in result.text
    assert "第二场初版。" not in result.text
    assert result.scenes[1].rewrite_count == 1
    assert result.scenes[0].rewrite_count == 0
    assert result.scenes[2].rewrite_count == 0


def test_rewrite_prompt_carries_editor_directive(monkeypatch):
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "初版。", _fail_verdict(), "重写版。", _verdict(),
         "三。", _verdict(), "四。", _verdict()],
    )
    _compose(db, project)
    rewrite_prompt = adapter.calls[3]
    assert "他很紧张" in rewrite_prompt       # 编辑意见里的证据原句
    assert "手抖" in rewrite_prompt           # 改法
    assert "重写" in rewrite_prompt


def test_rewrite_capped_at_two(monkeypatch):
    """连续不过 → 重写 2 次后接受当前版本(共 3 次生成)。"""
    db, project, adapter = _setup(
        monkeypatch,
        [
            _SCENE_JSON,
            "v0。", _fail_verdict(),
            "v1。", _fail_verdict(),
            "v2。", _fail_verdict(),
            "第二场。", _verdict(),
            "第三场。", _verdict(),
        ],
    )
    result = _compose(db, project)

    assert result.scenes[0].rewrite_count == sc.MAX_SCENE_REWRITES
    assert result.scenes[0].status == "rejected"
    assert result.stats["rejected"] == 1
    # 最终接受的是 v2(最后一次生成的),不是最初的 v0
    assert "v2。" in result.text
    assert "v0。" not in result.text


def test_rejected_scene_note_explains_why(monkeypatch):
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "v0。", _fail_verdict(),
         "v1。", _fail_verdict(), "v2。", _fail_verdict(),
         "第二场。", _verdict(), "第三场。", _verdict()],
    )
    result = _compose(db, project)
    note = result.scenes[0].accept_note
    assert "连续" in note and "未通过" in note
    assert "emotion_fit" in note


def test_rewrite_snapshot_kept(monkeypatch):
    """每次重写前存一版快照,供回溯与 diff 验收。"""
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON,
         "第一场。", _verdict(),
         "初版。", _fail_verdict(), "重写版。", _verdict(),
         "第三场。", _verdict()],
    )
    result = _compose(db, project)

    from app.db.models import SceneVersion

    rows = (
        db.query(SceneVersion)
        .filter(SceneVersion.scene_id == result.scenes[1].id)
        .order_by(SceneVersion.version)
        .all()
    )
    # v1 是切分时建的空快照,v2 是重写前的(即「初版。」)
    assert len(rows) >= 2
    rewritten = [r for r in rows if r.source == "rewritten"]
    assert rewritten and rewritten[0].content == "初版。"
    # 快照是重写前的版本,与重写后的正文不同
    assert result.scenes[1].content.strip() == "重写版。"


# ---------- 失败隔离 ----------

def test_generation_failure_does_not_kill_chapter(monkeypatch):
    """第 2 场生成抛异常 → 该场标 rejected,第 1/3 场照常完成。"""
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场。", _verdict(),
         RuntimeError("上游 502"),
         "第三场。", _verdict()],
    )
    result = _compose(db, project)

    assert result.stats["scene_count"] == 3
    assert 2 in result.stats["failed_seqs"]
    assert "第一场。" in result.text
    assert "第三场。" in result.text
    assert result.scenes[1].status == "rejected"
    assert "生成失败" in result.scenes[1].accept_note


def test_accept_degraded_does_not_trigger_rewrite(monkeypatch):
    """验收降级(输出解析失败)→ 不重写,标 drafted 待人工。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场。", "我不是 JSON",
         "第二场。", _verdict(), "第三场。", _verdict()],
    )
    result = _compose(db, project)

    assert result.scenes[0].status == "drafted"
    assert result.scenes[0].rewrite_count == 0
    assert result.stats["rewrites"] == 0
    # 调用次数证明没有重写
    assert len(adapter.calls) == 7


# ---------- 章级重写轮 ----------

def test_revision_directive_skips_acceptance(monkeypatch):
    """章级重写轮不做逐场验收(目标是「按意见改对」,不是重新判定)。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "改后的第一场。", "改后的第二场。", "改后的第三场。"],
    )
    result = _compose(db, project, revision_directive="把主角写得更狠一点")

    # 只有 1 次切分 + 3 次生成,没有验收调用
    assert len(adapter.calls) == 4
    assert all(s.status == "drafted" for s in result.scenes)
    assert result.stats["rewrites"] == 0


def test_revision_directive_only_first_scene(monkeypatch):
    """章级意见只在第一场注入一次,后续场靠「上一场尾部」自然承接。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "一。", "二。", "三。"],
    )
    _compose(db, project, revision_directive="独特意见标记XYZ")
    assert "独特意见标记XYZ" in adapter.calls[1]     # 第一场
    assert "独特意见标记XYZ" not in adapter.calls[2]  # 第二场
    assert "独特意见标记XYZ" not in adapter.calls[3]  # 第三场


def test_previous_scene_tail_injected_for_stitching(monkeypatch):
    """第二场的 prompt 必须带上第一场正文的尾部。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "第一场的最后一句话在这里。", _verdict(),
         "第二场。", _verdict(), "第三场。", _verdict()],
    )
    _compose(db, project)
    # calls[0]=切分, [1]=第一场, [3]=第二场
    assert "上一场结尾" in adapter.calls[3]
    assert "第一场的最后一句话" in adapter.calls[3]
    # 第一场不该有上一场块
    assert "上一场结尾" not in adapter.calls[1]


# ---------- 复用既有场景卡 ----------

def test_reuses_existing_scene_cards(monkeypatch):
    """已有场景卡时不重切:第二次调用不再打切分 LLM。"""
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "一。", _verdict(), "二。", _verdict(), "三。", _verdict(),
         "一。", _verdict(), "二。", _verdict(), "三。", _verdict()],
    )
    _compose(db, project)
    first_calls = len(adapter.calls)
    _compose(db, project)
    # 第二次少了 1 次切分调用
    assert len(adapter.calls) - first_calls == 6


def test_replan_forces_resplit(monkeypatch):
    db, project, adapter = _setup(
        monkeypatch,
        [_SCENE_JSON, "一。", _verdict(), "二。", _verdict(), "三。", _verdict(),
         _SCENE_JSON, "甲。", _verdict(), "乙。", _verdict(), "丙。", _verdict()],
    )
    _compose(db, project)
    result = _compose(db, project, replan=True)
    assert result.stats["scene_count"] == 3
    # 重切后旧卡被软删
    from app.db.models import Scene

    discarded = (
        db.query(Scene)
        .filter(Scene.project_id == project.id, Scene.status == "discarded")
        .count()
    )
    assert discarded == 3


# ---------- stats ----------

def test_stats_shape(monkeypatch):
    db, project, _ = _setup(
        monkeypatch,
        [_SCENE_JSON, "一。", _verdict(), "二。", _fail_verdict(),
         "二改。", _verdict(), "三。", _verdict()],
    )
    result = _compose(db, project)
    for key in ("scene_count", "accepted", "rejected", "failed_seqs", "rewrites", "words"):
        assert key in result.stats
    assert result.stats["accepted"] == 3
    assert result.stats["words"] == len(result.text)
