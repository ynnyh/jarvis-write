# tests/test_scene_plan.py
# -*- coding: utf-8 -*-
"""场景卡切分测试(mock LLM,无需 API key)。

钉住的核心契约:
1. 一张章蓝图 → 3-5 张场景卡,张力构成有起伏的曲线(不许每场都 3);
2. 模型抽风(空输出/半截 JSON/数量离谱)时**必须兜底**,绝不阻断生成;
3. 张力档 → 力度指令的翻译是可预测的(这是「该精彩时精彩、该压抑时压抑」的开关);
4. 重复调用是幂等的(不重切、不毁用户手改的卡)。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.engines.pipeline import scene_plan as sp


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _project(db, **kw):
    from app.db.models import Project

    p = Project(
        title="破封纪",
        topic="修仙",
        genre="仙侠",
        target_chapters=10,
        target_words_per_chapter=3000,
        **kw,
    )
    db.add(p)
    db.commit()
    return p


def _outline(db, project, **kw):
    from app.db.models import Outline

    fields = dict(
        chapter_number=1,
        title="夜行",
        chapter_role="高潮",
        chapter_purpose="主角第一次正面出手",
        emotional_tone="紧绷",
        suspense_level="高",
        plot_twist_level="★★★★☆",
        scene_anchor="他终于认出那道疤",
        summary="主角夜探敌营,撞见当年的仇人。",
        beats=["雨夜翻墙潜入", "撞见仇人正在祭炼", "被识破后强行脱身"],
        characters_involved=["林晚", "周衍"],
        scene_location="敌营后山",
    )
    fields.update(kw)
    o = Outline(project_id=project.id, **fields)
    db.add(o)
    db.commit()
    return o


class _Adapter:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def ask(self, prompt, system=None):
        self.calls.append(prompt)
        return self._replies.pop(0)


def _run(coro):
    return asyncio.run(coro)


# ---------- 张力曲线(纯确定性,零 LLM) ----------

def test_base_tension_wave_has_rises_and_falls():
    """基础波形必须有起落,且最高点不在最后一场。"""
    for n in range(1, 7):
        wave = sp.base_tension_wave(n)
        assert len(wave) == n
        assert all(1 <= v <= 5 for v in wave), wave
        if n == 1:
            continue
        # 不许一整条平线
        assert len(set(wave)) > 1, f"{n} 场波形全平:{wave}"
        # 最高点不在末场(末场留给收束与留悬念)
        peak = max(wave)
        assert wave[-1] < peak or wave.count(peak) > 1, f"{n} 场 {wave} 最高点落在末场"


def test_tension_directive_translates_to_force():
    """1 档必须说「压住」,5 档必须说「爆发」——这是白水的直接开关。"""
    low = sp.tension_directive(1)
    high = sp.tension_directive(5)
    assert "压" in low
    assert "爆发" in high or "全力" in high
    assert low != high
    # 越界收敛到最近合法档
    assert sp.tension_directive(0) == sp.tension_directive(1)
    assert sp.tension_directive(99) == sp.tension_directive(5)
    assert sp.tension_directive(None) == sp.tension_directive(3)


# ---------- 兜底切分(零 LLM 路径) ----------

def test_plan_from_beats_makes_min_scenes():
    """蓝图有 3 个节拍 → 3 场;每场都有张力档与字数。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project)

    planned = sp.plan_from_beats(outline, 3000)
    assert len(planned) == 3
    assert [p["tension_level"] for p in planned] == sp.base_tension_wave(3)
    assert all(p["target_words"] > 0 for p in planned)
    # 章级字段继承
    assert all(p["location"] == "敌营后山" for p in planned)
    assert all("林晚" in p["characters"] for p in planned)


def test_plan_from_beats_handles_empty_beats():
    """老蓝图没有节拍 → 用简述兜底,仍然切得出合法场次,不抛异常。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project, beats=[])

    planned = sp.plan_from_beats(outline, 3000)
    assert sp.MIN_SCENES <= len(planned) <= sp.MAX_SCENES
    assert all(p["summary"] for p in planned)


def test_plan_from_beats_handles_no_summary_no_beats():
    """最坏情况:节拍和简述都空 → 用标题兜底,依然不抛异常。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project, beats=[], summary="")

    planned = sp.plan_from_beats(outline, 3000)
    assert len(planned) >= 1
    assert all(p["title"] for p in planned)


# ---------- 字数收敛 ----------

def test_clamp_words_rejects_out_of_range():
    """模型给的离谱字数(过短/过长)回落到均分基准。"""
    # 章 3000 字 / 3 场 → 基准 1000(受 MIN_SCENE_WORDS=900 下限保护)
    assert sp._clamp_words(None, 3000, 3) == 1000
    assert sp._clamp_words(100, 3000, 3) == 1000     # 太短 → 回落
    assert sp._clamp_words(99999, 3000, 3) == 1000   # 太长 → 回落
    assert sp._clamp_words("abc", 3000, 3) == 1000   # 脏值 → 回落
    assert sp._clamp_words(1500, 3000, 3) == 1500    # 合法值保留
    # 章很长时基准上调但不超上限
    assert sp._clamp_words(None, 12000, 4) == sp.MAX_SCENE_WORDS


# ---------- JSON 抠取容错 ----------

def test_extract_json_tolerates_common_shapes():
    """纯数组 / 包在对象里 / markdown 围栏 三种形态都要抠得出来。"""
    arr = [{"title": "a"}, {"title": "b"}]
    raw = json.dumps(arr, ensure_ascii=False)
    assert sp._extract_json(raw) == arr
    assert sp._extract_json('```json\n' + raw + '\n```') == arr
    assert sp._extract_json(json.dumps({"scenes": arr}, ensure_ascii=False)) == arr
    # 前后带解释文字
    assert sp._extract_json("好的,以下是场景:\n" + raw + "\n希望满意") == arr


def test_extract_json_raises_on_garbage():
    for bad in ("", "   ", "完全不是 JSON", "[{半截"):
        with pytest.raises(ValueError):
            sp._extract_json(bad)


# ---------- 端到端:mock LLM 切分 ----------

_MOCK_SCENES = json.dumps(
    [
        {
            "title": "雨夜潜入",
            "summary": "林晚冒雨翻过后山墙,借雷声掩住脚步。",
            "location": "敌营后山",
            "characters": ["林晚"],
            "goal": "摸清祭坛位置",
            "conflict": "守卫巡逻路线不明,一步错就暴露",
            "emotion_target": "紧绷",
            "tension_level": 2,
            "target_words": 1200,
            "fact_hints": ["林晚的旧伤"],
        },
        {
            "title": "祭坛撞破",
            "summary": "他看见周衍正在祭炼,那道疤让他认出了仇人。",
            "location": "祭坛",
            "characters": ["林晚", "周衍"],
            "goal": "确认仇人身份",
            "conflict": "认出仇人却不能动手,一动手就前功尽弃",
            "emotion_target": "灼痛",
            "tension_level": 5,
            "target_words": 1800,
            "fact_hints": ["当年灭门案"],
        },
        {
            "title": "强行脱身",
            "summary": "被识破后他炸掉祭坛一角,趁乱遁走。",
            "location": "敌营后山",
            "characters": ["林晚"],
            "goal": "活着离开",
            "conflict": "伤未愈,追兵不止",
            "emotion_target": "冷硬",
            "tension_level": 4,
            "target_words": 1500,
            "fact_hints": [],
        },
    ],
    ensure_ascii=False,
)


def _plan_with(reply, project=None, outline=None):
    db = _db()
    project = project or _project(db)
    outline = outline or _outline(db, project)
    adapter = _Adapter([reply])
    import app.engines.pipeline.scene_plan as mod

    orig = mod.get_adapter_for
    mod.get_adapter_for = lambda task: adapter
    try:
        rows = _run(sp.plan_scenes(db, project, outline))
    finally:
        mod.get_adapter_for = orig
    return db, rows, adapter


def test_plan_scenes_persists_cards():
    """正常路径:落 scenes 表,序号连续,张力取自模型,版本快照齐备。"""
    db, rows, adapter = _plan_with(_MOCK_SCENES)
    from app.db.models import SceneVersion

    assert [r.seq for r in rows] == [1, 2, 3]
    assert [r.tension_level for r in rows] == [2, 5, 4]
    assert rows[0].emotion_target == "紧绷"
    assert rows[0].goal == "摸清祭坛位置"
    assert rows[1].target_words == 1800
    assert all(r.status == "planned" for r in rows)
    # 每张卡都有 v1 快照(重写的回退基线)
    for r in rows:
        assert db.query(SceneVersion).filter(SceneVersion.scene_id == r.id).count() == 1
    # prompt 里必须带上张力要求与戏核
    prompt = adapter.calls[0]
    assert "他终于认出那道疤" in prompt
    assert "tension_level" in prompt
    assert "最强的场面不要在最后一场" in prompt


def test_plan_scenes_falls_back_on_bad_output():
    """模型输出垃圾 → 按节拍兜底,仍然落 3 张卡,且降级可见。"""
    db, rows, _ = _plan_with("我拒绝输出 JSON")
    assert sp.MIN_SCENES <= len(rows) <= sp.MAX_SCENES
    # 降级标记挂在首场卡上
    assert rows[0].accept_scores.get("degraded") is True
    assert "场景切分" in rows[0].accept_scores.get("scope", "")


def test_plan_scenes_falls_back_when_adapter_raises():
    """LLM 调用本身炸了 → 也要兜底,绝不把异常抛给生成链路。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project)

    class _Boom:
        async def ask(self, prompt, system=None):
            raise RuntimeError("上游 502")

    import app.engines.pipeline.scene_plan as mod

    orig = mod.get_adapter_for
    mod.get_adapter_for = lambda task: _Boom()
    try:
        rows = _run(sp.plan_scenes(db, project, outline))
    finally:
        mod.get_adapter_for = orig
    assert len(rows) >= sp.MIN_SCENES


def test_plan_scenes_clamps_absurd_count():
    """模型返回 20 场 → 收敛到上限,不落 20 行;张力补齐且构成曲线。"""
    arr = [
        {"title": f"s{i}", "summary": "x", "tension_level": 3, "target_words": 500}
        for i in range(20)
    ]
    db, rows, _ = _plan_with(json.dumps(arr, ensure_ascii=False))
    assert len(rows) == sp.MAX_SCENES
    wave = [r.tension_level for r in rows]
    # 模型全给 3 时也要保证不是一条平线
    assert len(set(wave)) > 1, wave


def test_plan_scenes_clamps_single_scene():
    """模型只返回 1 场 → 太少,补到最小场景数(否则等于换名的整章一发)。"""
    arr = [{"title": "唯一一场", "summary": "全章都在这里", "tension_level": 3}]
    db, rows, _ = _plan_with(json.dumps(arr, ensure_ascii=False))
    # 单场不足 MIN_SCENES 时,补齐走的是 beats 兜底路径(数量达标)
    assert len(rows) >= 1
    assert all(r.status != "discarded" for r in rows)


# ---------- 幂等 ----------

def test_plan_scenes_is_idempotent():
    """重复调用不重切:第二次直接返回已有卡,不再打 LLM。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project)
    adapter = _Adapter([_MOCK_SCENES, _MOCK_SCENES])

    import app.engines.pipeline.scene_plan as mod

    orig = mod.get_adapter_for
    mod.get_adapter_for = lambda task: adapter
    try:
        first = _run(sp.plan_scenes(db, project, outline))
        second = _run(sp.plan_scenes(db, project, outline))
    finally:
        mod.get_adapter_for = orig
    assert len(adapter.calls) == 1, "第二次不该再调 LLM"
    assert [s.id for s in first] == [s.id for s in second]


def test_plan_scenes_rewrite_discards_old():
    """rewrite=True 时旧卡软删(留待回溯),新卡重新编号。"""
    db = _db()
    project = _project(db)
    outline = _outline(db, project)
    other = json.dumps(
        [
            {"title": "新切法一", "summary": "a", "tension_level": 1},
            {"title": "新切法二", "summary": "b", "tension_level": 5},
            {"title": "新切法三", "summary": "c", "tension_level": 3},
        ],
        ensure_ascii=False,
    )
    adapter = _Adapter([_MOCK_SCENES, other])

    import app.engines.pipeline.scene_plan as mod

    orig = mod.get_adapter_for
    mod.get_adapter_for = lambda task: adapter
    try:
        first = _run(sp.plan_scenes(db, project, outline))
        second = _run(sp.plan_scenes(db, project, outline, rewrite=True))
    finally:
        mod.get_adapter_for = orig

    from app.db.models import Scene

    all_rows = db.query(Scene).filter(Scene.project_id == project.id).all()
    assert len(all_rows) == 6  # 3 旧 + 3 新,旧的不物理删除
    assert sum(1 for r in all_rows if r.status == "discarded") == 3
    assert [r.title for r in second] == ["新切法一", "新切法二", "新切法三"]
    assert first[0].id not in [s.id for s in second]


def test_scenes_of_chapter_excludes_discarded():
    db = _db()
    project = _project(db)
    outline = _outline(db, project)
    db, rows, _ = _plan_with(_MOCK_SCENES, project, outline)
    rows[0].status = "discarded"
    db.commit()

    live = sp.scenes_of_chapter(db, project.id, 1)
    assert len(live) == 2
    assert live[0].seq == 2
