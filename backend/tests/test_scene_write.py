# tests/test_scene_write.py
# -*- coding: utf-8 -*-
"""场景级生成与验收测试(mock LLM)。

钉住的核心契约:
1. 一次调用只写一场,上一场尾部作为缝接材料注入;
2. 验收只判三项硬指标(emotion_fit/goal_done/concreteness),不重复判章级的事;
3. 验收降级(输出解析失败)不触发重写——重写解决不了解析问题;
4. 拼接用字符 offset,anchors 与正文严格对应;
5. 场景小标题会被清掉(场景级生成明确要求「不要写标题」)。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.engines.pipeline import scene_write as sw


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class _Adapter:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def ask(self, prompt, system=None):
        self.calls.append(prompt)
        if not self._replies:
            return ""
        return self._replies.pop(0)


def _patch(monkeypatch, adapter, module):
    monkeypatch.setattr(module, "get_adapter_for", lambda task: adapter)


def _mk(db, **kw):
    from app.db.models import Project, Scene

    p = Project(
        title="破封纪", topic="修仙", genre="仙侠",
        target_chapters=10, target_words_per_chapter=3000,
    )
    db.add(p)
    db.commit()
    fields = dict(
        project_id=p.id, outline_id=1, chapter_number=6, seq=1,
        title="祭坛撞破", summary="林晚看见周衍祭炼玄铁令。",
        location="祭坛", characters=["林晚"], goal="确认仇人身份",
        conflict="不能动手", emotion_target="灼痛", tension_level=5,
        target_words=1500,
    )
    fields.update(kw)
    s = Scene(**fields)
    db.add(s)
    db.commit()
    return p, s


# ---------- 元信息清理 ----------

def test_strip_scene_meta_removes_headings():
    assert sw._strip_scene_meta("# 第3场\n\n他推开门。") == "他推开门。"
    assert sw._strip_scene_meta("第3场 - 祭坛撞破\n他推开门。") == "他推开门。"
    assert sw._strip_scene_meta("【祭坛撞破】\n他推开门。") == "他推开门。"
    assert sw._strip_scene_meta("祭坛撞破\n他推开门。") == "他推开门。"


def test_strip_scene_meta_keeps_real_first_sentence():
    """正文首句不许被误删——即使它很短。"""
    body = "他推开门。屋里空着。"
    assert sw._strip_scene_meta(body) == body
    # 带句末标点的短句不是标题
    assert sw._strip_scene_meta("谁?\n他推开门。") == "谁?\n他推开门。"


def test_strip_scene_meta_never_eats_whole_scene():
    """单行正文无论多短都必须留下 —— 删了就等于把这一场清空。

    这是实测踩出来的坑:初版按 `^第N场` 一刀切,「第一场。」被整句吃掉,
    整章拼接结果成了「\\n\\n\\n\\n」(四个空场)。同样的道理也适用于「甲」「他死了」
    这种短到极致但合法的正文。
    """
    for body in ["第一场。", "第一场正文内容。", "甲", "他死了", "祭坛撞破"]:
        assert sw._strip_scene_meta(body) == body, body


def test_strip_scene_meta_bare_title_only_when_body_follows():
    """长度启发式只在这行后面还有正文时才生效。"""
    assert sw._strip_scene_meta("祭坛撞破\n他推开门。") == "他推开门。"
    # 单独一行就是正文本身,不猜
    assert sw._strip_scene_meta("祭坛撞破") == "祭坛撞破"


def test_strip_scene_meta_handles_empty():
    assert sw._strip_scene_meta("") == ""
    assert sw._strip_scene_meta("\n\n  \n") == ""


# ---------- 尾部落注 ----------

def test_tail_of_truncates_with_marker():
    long = "甲" * 1000
    tail = sw._tail_of(long, 100)
    assert tail.startswith("……")
    assert len(tail) == 102  # "……"(2 字符)+ 100 字
    assert sw._tail_of("短文本", 100) == "短文本"
    assert sw._tail_of("", 100) == ""


def test_word_target_line_uses_scene_target():
    class _S:
        target_words = 1800

    line = sw._word_target_line(3000, _S())
    assert "1800" in line
    assert "1200" in line  # 下限 = 1800*2/3


# ---------- 场景生成 ----------

def _write(monkeypatch, replies, **scene_kw):
    db = _db()
    project, scene = _mk(db, **scene_kw)
    adapter = _Adapter(replies)
    _patch(monkeypatch, adapter, sw)
    text = asyncio.run(
        sw.write_scene(
            db, project, scene,
            chapter_number=6, scene_total=3,
            style_block="【文风】克制、具体",
            deai_rules="【禁令】不许套话",
            rolling_summary="前情:主角潜入敌营。",
            recent_tail="上一章结尾原文。",
            handoff_block="【契约】主角带着旧伤。",
            scene_anchor="他终于认出那道疤",
            chapter_summary="夜探敌营,撞见仇人。",
            chapter_title="夜行",
        )
    )
    return db, project, scene, adapter, text


def test_write_scene_injects_tension_directive(monkeypatch):
    """张力档 5 → prompt 里必须出现「全力爆发」的力度指令。"""
    db, project, scene, adapter, text = _write(monkeypatch, ["正文内容。"])
    p = adapter.calls[0]
    assert "全力爆发" in p or "爆发" in p
    assert "5/5" in p or "张力档" in p or "力度" in p


def test_write_scene_injects_low_tension_as_suppress(monkeypatch):
    """张力档 1 → 必须说「压住」,不能同样说爆发。"""
    db, project, scene, adapter, _ = _write(monkeypatch, ["正文。"], tension_level=1)
    p = adapter.calls[0]
    assert "压住" in p
    assert "全力爆发" not in p


def test_write_scene_carries_scene_card_fields(monkeypatch):
    db, project, scene, adapter, _ = _write(monkeypatch, ["正文。"])
    p = adapter.calls[0]
    assert "祭坛撞破" in p          # scene_title
    assert "祭坛" in p              # scene_location
    assert "确认仇人身份" in p       # scene_goal
    assert "灼痛" in p              # emotion_target
    assert "他终于认出那道疤" in p   # 章戏核
    assert "只写" in p and "这一场" in p  # 只写这一场的硬要求


def test_write_scene_strips_heading(monkeypatch):
    db, project, scene, adapter, text = _write(monkeypatch, ["第1场 - 祭坛撞破\n他推开门。"])
    assert text == "他推开门。"


def test_write_scene_passes_previous_tail_for_stitching(monkeypatch):
    """有上一场正文时,prompt 必须带上它的尾部(缝接的一手材料)。"""
    db = _db()
    project, scene = _mk(db)
    adapter = _Adapter(["正文。"])
    _patch(monkeypatch, adapter, sw)
    asyncio.run(
        sw.write_scene(
            db, project, scene,
            chapter_number=6, scene_total=3,
            style_block="", deai_rules="",
            rolling_summary="", recent_tail="", handoff_block="",
            scene_anchor="", chapter_summary="", chapter_title="夜行",
            previous_text="上一场的最后一句:" + "尾" * 50,
        )
    )
    p = adapter.calls[0]
    assert "上一场结尾" in p
    assert "尾" * 10 in p


def test_write_scene_omits_tail_block_when_no_previous(monkeypatch):
    """第一场没有上一场 → 不该出现空的「上一场结尾」块。"""
    db, project, scene, adapter, _ = _write(monkeypatch, ["正文。"])
    assert "上一场结尾" not in adapter.calls[0]


def test_write_scene_retrieval_stats_recorded(monkeypatch):
    """检索诊断随场景卡落库(前端可回显「这一场参考了什么」)。"""
    db, project, scene, _, _ = _write(monkeypatch, ["正文。"])
    assert "retrieval" in (scene.accept_scores or {})


def test_write_scene_marks_status_drafting(monkeypatch):
    db, project, scene, _, _ = _write(monkeypatch, ["正文。"])
    assert scene.status == "drafting"


def test_write_scene_rewrite_uses_directive(monkeypatch):
    """带 revision_directive 时走重写 prompt,并把编辑意见注入。"""
    db = db2 = _db()
    project, scene = _mk(db2)
    scene.content = "上一版正文:他很紧张地站在那里。"
    db2.commit()
    adapter = _Adapter(["重写后的正文。"])
    _patch(monkeypatch, adapter, sw)
    text = asyncio.run(
        sw.write_scene(
            db2, project, scene,
            chapter_number=6, scene_total=3,
            style_block="", deai_rules="",
            rolling_summary="", recent_tail="", handoff_block="",
            scene_anchor="", chapter_summary="", chapter_title="夜行",
            revision_directive="1. 原文「他很紧张」:情绪直陈,改成动作",
        )
    )
    p = adapter.calls[0]
    assert "他很紧张" in p
    assert "重写" in p
    assert text == "重写后的正文。"


# ---------- 场景验收 ----------

_VERDICT_OK = json.dumps(
    {
        "scores": {"emotion_fit": 8, "goal_done": 8, "tension_fit": 8,
                   "concreteness": 8, "prose": 8},
        "comment": "这一场到位",
        "suggestions": [],
    },
    ensure_ascii=False,
)

_VERDICT_FAIL = json.dumps(
    {
        "scores": {"emotion_fit": 4, "goal_done": 8, "tension_fit": 7,
                   "concreteness": 5, "prose": 6},
        "comment": "情绪没到位,画面太虚",
        "suggestions": [
            {"evidence": "他很紧张", "issue": "情绪直陈", "fix": "改成手在抖"},
        ],
    },
    ensure_ascii=False,
)


def _accept(monkeypatch, reply, threshold=7):
    db = _db()
    project, scene = _mk(db)
    scene.content = "正文内容。"
    db.commit()
    adapter = _Adapter([reply])
    _patch(monkeypatch, adapter, sw)
    verdict = asyncio.run(sw.accept_scene(db, scene, threshold))
    return verdict, adapter


def test_accept_scene_passes_when_all_hard_dims_ok(monkeypatch):
    verdict, _ = _accept(monkeypatch, _VERDICT_OK)
    assert verdict["passed"] is True
    assert verdict["degraded"] is False
    assert verdict["failing"] == []


def test_accept_scene_fails_on_low_emotion_and_concreteness(monkeypatch):
    verdict, _ = _accept(monkeypatch, _VERDICT_FAIL)
    assert verdict["passed"] is False
    assert set(verdict["failing"]) == {"emotion_fit", "concreteness"}
    # tension_fit 低(7 刚好达标)不该进 failing——它归章级主审
    assert "tension_fit" not in verdict["failing"]


def test_accept_scene_only_judges_three_hard_dims(monkeypatch):
    """tension_fit 与 prose 即使很低也不进场景级回调(归章级主审)。"""
    reply = json.dumps({
        "scores": {"emotion_fit": 9, "goal_done": 9, "tension_fit": 2,
                   "concreteness": 9, "prose": 1},
        "comment": "x", "suggestions": [],
    }, ensure_ascii=False)
    verdict, _ = _accept(monkeypatch, reply)
    assert verdict["passed"] is True, "场景级不该为 tension/prose 触发重写"


def test_accept_scene_degrades_on_bad_output(monkeypatch):
    """输出解析失败 → 记降级、passed=False,但 degraded=True 让调用方跳过重写。"""
    verdict, _ = _accept(monkeypatch, "我不是 JSON")
    assert verdict["passed"] is False
    assert verdict["degraded"] is True
    assert verdict["scope"] == "场景验收"
    assert "解析" in verdict["comment"] or "解析" in verdict["reason"]


def test_accept_scene_degrades_on_missing_scores(monkeypatch):
    verdict, _ = _accept(monkeypatch, json.dumps({"comment": "缺 scores"}))
    assert verdict["degraded"] is True


def test_accept_scene_tolerates_dirty_score_values(monkeypatch):
    reply = json.dumps({
        "scores": {"emotion_fit": "8", "goal_done": "abc",
                   "tension_fit": None, "concreteness": 7, "prose": 7},
        "comment": "", "suggestions": [],
    }, ensure_ascii=False)
    verdict, _ = _accept(monkeypatch, reply)
    assert verdict["scores"]["emotion_fit"] == 8
    assert "goal_done" not in verdict["scores"]
    # 脏值当 0 分 → 未达标
    assert "goal_done" in verdict["failing"]


def test_accept_scene_injects_tension_directive(monkeypatch):
    _, adapter = _accept(monkeypatch, _VERDICT_OK)
    p = adapter.calls[0]
    assert "全力爆发" in p or "爆发" in p
    assert "5" in p


def test_accept_scene_prompt_includes_body(monkeypatch):
    _, adapter = _accept(monkeypatch, _VERDICT_OK)
    assert "正文内容。" in adapter.calls[0]


# ---------- 重写指令构造 ----------

def test_build_scene_directive_includes_suggestions():
    d = sw._build_scene_directive({
        "comment": "情绪没到位",
        "suggestions": [{"evidence": "他很紧张", "issue": "直陈情绪", "fix": "改成手抖"}],
    })
    assert "情绪没到位" in d
    assert "他很紧张" in d
    assert "手抖" in d


def test_build_scene_directive_has_fallback():
    d = sw._build_scene_directive({"comment": "", "suggestions": []})
    assert "重写" in d


# ---------- 快照 ----------

def test_snapshot_scene_increments_version():
    db = _db()
    project, scene = _mk(db)
    scene.content = "第一版"
    db.commit()
    sw.snapshot_scene(db, scene, source="rewritten", note="验收未过")
    scene.content = "第二版"
    db.commit()
    sw.snapshot_scene(db, scene, source="rewritten", note="第二次")
    db.commit()

    from app.db.models import SceneVersion

    rows = (
        db.query(SceneVersion)
        .filter(SceneVersion.scene_id == scene.id)
        .order_by(SceneVersion.version)
        .all()
    )
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].content == "第一版"
    assert rows[1].content == "第二版"
    assert rows[1].note == "第二次"


# ---------- 拼接与锚点 ----------

def _scene_obj(content, **kw):
    from app.db.models import Scene

    s = Scene(project_id=1, outline_id=1, chapter_number=1, seq=kw.pop("seq", 1),
              content=content, **kw)
    return s


def test_join_scenes_anchors_match_text():
    a = _scene_obj("第一场正文。", seq=1)
    b = _scene_obj("第二场正文。", seq=2)
    c = _scene_obj("第三场正文。", seq=3)
    text, anchors = sw.join_scenes([a, b, c])

    assert len(anchors) == 3
    assert text[anchors[0][0]:anchors[0][1]] == "第一场正文。"
    assert text[anchors[1][0]:anchors[1][1]] == "第二场正文。"
    assert text[anchors[2][0]:anchors[2][1]] == "第三场正文。"
    # 场间以空行分隔
    assert anchors[1][0] > anchors[0][1]
    assert text[anchors[0][1]:anchors[1][0]] == sw.SCENE_JOIN_SEPARATOR


def test_join_scenes_handles_empty_scene():
    a = _scene_obj("第一场。", seq=1)
    b = _scene_obj("", seq=2)
    c = _scene_obj("第三场。", seq=3)
    text, anchors = sw.join_scenes([a, b, c])
    assert text[anchors[0][0]:anchors[0][1]] == "第一场。"
    assert text[anchors[1][0]:anchors[1][1]] == ""
    assert text[anchors[2][0]:anchors[2][1]] == "第三场。"


def test_join_scenes_single():
    text, anchors = sw.join_scenes([_scene_obj("独角戏。", seq=1)])
    assert text == "独角戏。"
    assert anchors == [(0, 4)]


def test_join_scenes_strips_whitespace():
    text, anchors = sw.join_scenes([_scene_obj("  空格前后  ", seq=1)])
    assert text == "空格前后"
    assert anchors[0] == (0, 4)


# ---------- 常量契约 ----------

def test_rewrite_cap_is_two():
    """D4 决策:重写封顶 2 次(第 3 次基本是同一份 prompt 再抽一次签)。"""
    assert sw.MAX_SCENE_REWRITES == 2


# ---------- 反转预备接线(§1.4) ----------

def _seed_twist(db, project):
    """在库里放一份「转折章 + 有素材」的读者认知数据。"""
    from app.db.models import Entity, Fact, KnowledgeState, Outline

    lin = Entity(project_id=project.id, entity_type="character", name="林昭",
                 aliases=[], base_profile={}, retired=False)
    shen = Entity(project_id=project.id, entity_type="character", name="沈砚",
                  aliases=[], base_profile={}, retired=False)
    db.add_all([lin, shen])
    db.flush()

    f = Fact(project_id=project.id, entity_id=lin.id, fact_type="state",
             content="山火是国师策划的", valid_from=3, valid_until=None,
             importance="critical", source_chapter=3)
    db.add(f)
    db.flush()
    # 沈砚知道、读者不知道 → 信息差;同时也构成「压着的底牌」
    db.add(KnowledgeState(project_id=project.id, fact_id=f.id, knower=str(shen.id),
                          known_from_chapter=4, knower_state="known"))
    o = Outline(project_id=project.id, chapter_number=6, title="夜行",
                plot_twist_level="★★★★★", chapter_role="真相揭露")
    db.add(o)
    db.commit()
    return o


def test_write_scene_injects_twist_prep_on_twist_scene(monkeypatch):
    """转折章 + 场景卡含转折词 → prompt 里要有反转预备块。

    这个用例是补网:此前 `names` 的作用域 bug 让 `write_scene` 走不对称分支时抛
    UnboundLocalError,而全量测试**没有**任何用例把「转折章 + 有素材」这条路走通,
    于是全绿却漏了。凡是有条件分支的注入路径,都得有一条走到它的用例。
    """
    db = _db()
    from app.db.models import Scene, Project

    p = Project(title="破封纪", topic="修仙", genre="仙侠",
                target_chapters=10, target_words_per_chapter=3000)
    db.add(p)
    db.commit()
    o = _seed_twist(db, p)

    scene = Scene(project_id=p.id, outline_id=o.id, chapter_number=6, seq=1,
                  title="旧疤", location="祠堂", characters=["林昭"],
                  goal="撞破真相", conflict="真相揭露的那一刻",
                  emotion_target="窒息", tension_level=5, target_words=1500)
    db.add(scene)
    db.commit()

    adapter = _Adapter(["正文。"])
    _patch(monkeypatch, adapter, sw)
    asyncio.run(
        sw.write_scene(
            db, p, scene,
            chapter_number=6, scene_total=3,
            style_block="", deai_rules="", rolling_summary="", recent_tail="",
            handoff_block="", scene_anchor="", chapter_summary="", chapter_title="夜行",
            outline=o,
        )
    )
    p_prompt = adapter.calls[0]
    assert "反转预备" in p_prompt
    assert "不要靠人物开口解释真相" in p_prompt
    # 信息差的人名要解析出来,不能是「角色2」
    assert "沈砚" in p_prompt
    assert "角色" not in p_prompt.split("信息差")[1][:40]


def test_write_scene_omits_twist_prep_for_plain_scene(monkeypatch):
    """转折章的普通场次不注入:反转预备只落在承接反转的那一场。"""
    db = _db()
    from app.db.models import Scene, Project

    p = Project(title="破封纪", topic="修仙", genre="仙侠",
                target_chapters=10, target_words_per_chapter=3000)
    db.add(p)
    db.commit()
    o = _seed_twist(db, p)

    scene = Scene(project_id=p.id, outline_id=o.id, chapter_number=6, seq=2,
                  title="赶路", location="官道", characters=["林昭"],
                  goal="抵达", conflict="赶路中的沉默",   # 无转折词
                  emotion_target="沉闷", tension_level=2, target_words=1500)
    db.add(scene)
    db.commit()

    adapter = _Adapter(["正文。"])
    _patch(monkeypatch, adapter, sw)
    asyncio.run(
        sw.write_scene(
            db, p, scene,
            chapter_number=6, scene_total=3,
            style_block="", deai_rules="", rolling_summary="", recent_tail="",
            handoff_block="", scene_anchor="", chapter_summary="", chapter_title="夜行",
            outline=o,
        )
    )
    assert "反转预备" not in adapter.calls[0]


def test_write_scene_omits_twist_prep_for_non_twist_chapter(monkeypatch):
    """非转折章:即使有素材也不注入(不该每章都做反转)。"""
    db = _db()
    from app.db.models import Outline, Scene, Project

    p = Project(title="破封纪", topic="修仙", genre="仙侠",
                target_chapters=10, target_words_per_chapter=3000)
    db.add(p)
    db.commit()
    _seed_twist(db, p)
    plain = (
        db.query(Outline).filter(Outline.chapter_number == 6).first()
    )
    plain.plot_twist_level = "★☆☆☆☆"
    plain.chapter_role = "过渡"
    db.commit()

    scene = Scene(project_id=p.id, outline_id=plain.id, chapter_number=6, seq=1,
                  title="真相", location="客栈", characters=["林昭"],
                  goal="落店", conflict="真相揭露的传闻",  # 有词但章不是转折章
                  emotion_target="疲", tension_level=2, target_words=1500)
    db.add(scene)
    db.commit()

    adapter = _Adapter(["正文。"])
    _patch(monkeypatch, adapter, sw)
    asyncio.run(
        sw.write_scene(
            db, p, scene,
            chapter_number=6, scene_total=3,
            style_block="", deai_rules="", rolling_summary="", recent_tail="",
            handoff_block="", scene_anchor="", chapter_summary="", chapter_title="夜行",
            outline=plain,
        )
    )
    assert "反转预备" not in adapter.calls[0]
