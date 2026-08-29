# tests/test_continuity_gate.py
# -*- coding: utf-8 -*-
"""写后一致性门禁(Continuity Gate)测试(mock LLM,无需 API key)。

覆盖 docs/08 §5.4 + P0 第二棒任务:
- checker 升级:对照上章契约+结尾原文(prompt 注入);圣经为空走"仅对照上章"降级;
  三路对照源全空才跳过;severity 归一(critical→blocker,未知→minor);幻觉举证清空
- chapter_issues 落库幂等:purge 旧 open 重建;指纹失效的 ignored 清除,未失效保留
- 门禁判定:分级回炉(docs/08 §5.4)——门禁先行,blocker 先分诊定点修复
  (逐字+唯一锚替换,复查通过即出循环),修不掉回退整章重写;封顶 → quarantined
  (不抽圣经/不更新摘要/不提契约,issues 落 open);auto_revise 关 → 直接隔离;
  精修(校对+主审)只在门禁干净后跑,主审触发的重写也回门禁复查
- 第五维 continuity:blocker→4 / major→6 / minor→8 / 干净→9,纳入 judge_passed
- quarantined 出口:重写成功回 pending_review(旧 open 清账);gate-release 放行端点
  (issues 标 ignored + 状态回 pending_review + 补走抽取/摘要/契约)
- 连写队列遇 quarantined 即停
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

CONTRACT = {
    "in_story_time": "第三日 深夜",
    "location": "破庙内",
    "scene_continues": False,
    "characters": [
        {
            "name": "沈墨",
            "location": "破庙内",
            "physical": "左臂刀伤未愈",
            "emotional": "戒备、疲惫",
            "doing": "刚入睡",
            "knows": ["黑衣人来自听雨楼"],
            "unresolved_intent": "明日动身去渡口",
        }
    ],
    "open_threads": ["庙外脚步声未查明"],
    "time_jump_hint": "next_morning",
}
CONTRACT_JSON = json.dumps(CONTRACT, ensure_ascii=False)

CH1_TEXT = "夜深了,沈墨在破庙里睡去。" * 20
CH2_TEXT = "沈墨在破庙里醒来,看着篝火发呆。" * 20

BLOCKER_ISSUE = {
    "severity": "blocker",
    "type": "state",
    "description": "上章末刚入睡,本章开头却清醒发呆,无时间跳跃交代",
    "evidence": "沈墨在破庙里醒来",
    "conflicting_fact": "上章契约:沈墨 doing=刚入睡",
    "suggestion": "开头补一段时间流逝的交代",
}
BLOCKER_JSON = json.dumps({"issues": [BLOCKER_ISSUE]}, ensure_ascii=False)
CLEAN_JSON = '{"issues": []}'


def _make_db(with_ch1: bool = True, with_contract: bool = True, **project_kwargs):
    """独立内存库:一个项目 + 两章大纲 + (可选)第 1 章正文与契约。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="门禁测试书", target_chapters=2, target_words_per_chapter=3000,
        **project_kwargs,
    )
    db.add(project)
    db.flush()
    for n, title in ((1, "破庙夜宿"), (2, "破庙清晨")):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=title,
            chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
        ))
    db.flush()
    ch1 = None
    if with_ch1:
        ch1 = Chapter(
            project_id=project.id, outline_id=1, chapter_number=1,
            final_content=CH1_TEXT, word_count=len(CH1_TEXT), status="approved",
        )
        db.add(ch1)
        db.flush()
        if with_contract:
            db.add(ChapterState(
                chapter_id=ch1.id, contract=CONTRACT_JSON,
                content_hash=content_hash(CH1_TEXT), extract_status="ok",
            ))
    db.commit()
    return db, project, ch1


class _Adapter:
    """固定返回一条回复的假 LLM,记录全部 prompt。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


# ---------- checker:对照上章契约 + 结尾原文 ----------
def test_checker_compares_prev_contract_and_tail():
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db()
    adapter = _Adapter(BLOCKER_JSON)
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, CH2_TEXT))

    assert len(adapter.prompts) == 1
    prompt = adapter.prompts[0]
    # 上章契约注入(章末瞬态事实)+ 上章结尾原文注入
    assert "章末交接契约" in prompt
    assert "刚入睡" in prompt
    assert "第1章结尾" in prompt
    assert "沈墨在破庙里睡去" in prompt
    # 归一化输出:问题点/证据/建议/severity/类型齐全
    assert len(issues) == 1
    assert issues[0]["severity"] == "blocker"
    assert issues[0]["type"] == "state"
    assert issues[0]["evidence"] == "沈墨在破庙里醒来"
    assert issues[0]["suggestion"]


def test_checker_degrades_to_prev_chapter_when_bible_empty():
    """圣经为空不再跳过:无契约但有上章正文 → 仅对照上章结尾原文。"""
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db(with_contract=False)
    adapter = _Adapter(BLOCKER_JSON)
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 2, CH2_TEXT))

    assert len(adapter.prompts) == 1  # 没有跳过,LLM 被调用
    assert "仅对照上一章" in adapter.prompts[0]
    assert "第1章结尾" in adapter.prompts[0]
    assert len(issues) == 1


def test_checker_skips_only_when_no_sources_at_all():
    """第一章且圣经为空:没有任何可对照的事实源,直接返回空(不调 LLM)。"""
    from app.engines.consistency import checker as checker_mod

    db, project, _ch1 = _make_db(with_ch1=False)
    adapter = _Adapter(BLOCKER_JSON)
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        issues = asyncio.run(checker_mod.check_chapter(db, project.id, 1, CH2_TEXT))
    assert issues == []
    assert adapter.prompts == []


def test_normalize_issue_severity_and_evidence():
    from app.engines.consistency.checker import _normalize_issue

    text = "沈墨在破庙里醒来,看着篝火发呆。"
    # critical(旧 prompt 措辞)→ blocker;举证逐字在正文里 → 保留
    i = _normalize_issue(dict(BLOCKER_ISSUE, severity="critical"), text)
    assert i["severity"] == "blocker"
    assert i["evidence"] == "沈墨在破庙里醒来"
    # 未知 severity → minor(宁低估不误判隔离);未知 type → state
    i2 = _normalize_issue({"severity": "fatal", "type": "other", "description": "x"}, text)
    assert i2["severity"] == "minor"
    assert i2["type"] == "state"
    # 幻觉举证(正文里引不到)→ 清空
    i3 = _normalize_issue(
        {"severity": "major", "type": "state", "description": "x", "evidence": "正文里没有这句"},
        text,
    )
    assert i3["evidence"] == ""
    # 新增维度 ambient(环境氛围连续性)/ cast(凭空常驻角色)是合法类型,不被降级为 state
    for t in ("ambient", "cast"):
        it = _normalize_issue({"severity": "blocker", "type": t, "description": "x"}, text)
        assert it["type"] == t
        assert it["severity"] == "blocker"


# ---------- chapter_issues 落库幂等 ----------
def test_persist_issues_purge_open_rebuild_and_fingerprint():
    from app.db.models import Chapter, ChapterIssue
    from app.engines.consistency.checker import persist_issues
    from app.engines.editorial import content_hash

    db, project, _ch1 = _make_db()
    ch = Chapter(
        project_id=project.id, outline_id=2, chapter_number=2,
        final_content=CH2_TEXT, word_count=len(CH2_TEXT), status="approved",
    )
    db.add(ch)
    db.commit()

    persist_issues(db, ch, [BLOCKER_ISSUE], source="gate", text=CH2_TEXT)
    db.commit()
    assert db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).count() == 1

    # 同一来源再检查:旧 open purge 重建(数量不变,描述更新)
    updated = dict(BLOCKER_ISSUE, description="新表述的矛盾")
    persist_issues(db, ch, [updated, dict(BLOCKER_ISSUE, severity="minor", description="小问题")],
                   source="gate", text=CH2_TEXT)
    db.commit()
    rows = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).all()
    assert len(rows) == 2
    assert {r.description for r in rows} == {"新表述的矛盾", "小问题"}
    assert all(r.status == "open" for r in rows)
    assert all(r.content_hash == content_hash(CH2_TEXT) for r in rows)

    # 用户忽略了一条:指纹未变的 ignored 在下次检查时保留(不重报)
    ignored = rows[0]
    ignored.status = "ignored"
    db.commit()
    persist_issues(db, ch, [], source="gate", text=CH2_TEXT)
    db.commit()
    rows = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).all()
    assert len(rows) == 1 and rows[0].status == "ignored"

    # 正文被重写(指纹变化)→ 旧 ignored 不再生效,被清除
    persist_issues(db, ch, [], source="gate", text=CH2_TEXT + "重写后的新结尾。")
    db.commit()
    assert db.query(ChapterIssue).filter(ChapterIssue.chapter_id == ch.id).count() == 0


# ---------- 第五维 continuity ----------
def test_continuity_score_mapping():
    from app.engines.consistency.checker import continuity_score

    assert continuity_score([]) == 9
    assert continuity_score([{"severity": "minor"}]) == 8
    assert continuity_score([{"severity": "major"}, {"severity": "minor"}]) == 6
    assert continuity_score([{"severity": "blocker"}, {"severity": "major"}]) == 4


def test_judge_passed_with_continuity_dim():
    from app.engines.editorial import judge_passed

    high4 = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}
    # 无 continuity 键 → 维持四维旧行为(编辑部手动主审/旧快照兼容)
    assert judge_passed(high4, 7)
    # continuity 达标 → 过;低于阈值 → 不过(blocker 折算 4 必不过)
    assert judge_passed(dict(high4, continuity=9), 7)
    assert not judge_passed(dict(high4, continuity=6), 7)
    assert not judge_passed(dict(high4, continuity=4), 7)
    # 阈值调低时 continuity 也可过(但 blocker 仍由门禁一票否决,见流水线判定)
    assert judge_passed(dict(high4, continuity=4), 4)


# ---------- 门禁判定:回炉 / quarantined ----------
HIGH = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}


class _PipelineAdapter:
    """按 prompt 内容回复:草稿/定稿/契约/摘要;记录全部 prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        if "现在开始写" in prompt:
            return "草稿正文。" * 30
        if "修订后的" in prompt:
            return "定稿正文。" * 30
        if "场记" in prompt:
            return CONTRACT_JSON
        return "前情摘要。"


async def _fake_proofread(*a, **k):
    return {"issues": []}


async def _fake_preflight(*a, **k):
    """写前审核默认无警告(单独用例在 test_review_workflow 覆盖)。"""
    return []


async def _fake_review_high(*a, **k):
    return {"scores": dict(HIGH), "comment": "", "suggestions": []}


async def _fake_repair_no_fixes(*a, **k):
    """定点修复默认不给方案(→ 同轮转重写);patch 用例单独注入。"""
    return []


def _counting(fake):
    """包一层调用计数,断言「精修只在门禁干净后跑」。"""
    state = {"calls": 0}

    async def _inner(*a, **k):
        state["calls"] += 1
        return await fake(*a, **k)

    return _inner, state


def _scripted_repair(seq: list[list[dict]]):
    """按脚本依次返回定点修复方案;记录收到的正文。"""
    state = {"calls": 0, "texts": []}

    async def _inner(chapter_number, content, issues):
        state["calls"] += 1
        state["texts"].append(content)
        return seq.pop(0) if seq else []

    return _inner, state


def _run_generate(db, project, n, check_fn, extract_fn, adapter=None, repair_fn=None):
    """mock LLM 跑一遍 generate_chapter;check/extract/repair 由参数注入(脚本化)。"""
    from app.engines.pipeline import chapter as ch_mod

    adapter = adapter or _PipelineAdapter()
    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check_fn),
        patch.object(ch_mod, "extract_and_apply", new=extract_fn),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
        patch.object(ch_mod, "repair_chapter", new=repair_fn or _fake_repair_no_fixes),
    ):
        result = asyncio.run(ch_mod.generate_chapter(db, project, n))
    return adapter, result


class _ScriptedCheck:
    """按脚本依次返回门禁结果;记录收到的正文。"""

    def __init__(self, seq: list[list[dict]]):
        self._seq = list(seq)
        self.calls: list[str] = []

    async def __call__(self, db, project_id, chapter_number, chapter_text, rolling_summary=""):
        self.calls.append(chapter_text)
        return self._seq.pop(0) if self._seq else []


class _SpyExtract:
    """抽取哨兵:记录是否被调用(quarantined 时绝不能调到)。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, *a, **k):
        self.calls += 1
        return {"facts": 1}


class _AlwaysCheck:
    """每次检查都返回固定结果(回炉多少轮都一样,用于必隔离场景)。"""

    def __init__(self, issues: list[dict]):
        self._issues = issues
        self.calls = 0

    async def __call__(self, db, project_id, chapter_number, chapter_text, rolling_summary=""):
        self.calls += 1
        return self._issues


def test_gate_revises_on_blocker_then_passes():
    """首轮 blocker(默认无修复方案)→ 转整章重写 → 次轮干净 → pending_review,共享回炉计数。"""
    db, project, _ch1 = _make_db(review_max_revisions=3)
    check = _ScriptedCheck([[BLOCKER_ISSUE], []])
    extract = _SpyExtract()
    adapter, (chapter, issues, stats, _guard, review, _pf) = _run_generate(
        db, project, 2, check, extract
    )

    assert review["revision_rounds"] == 1
    assert review["passed"] is True
    assert review["scores"]["continuity"] == 9  # 末轮干净 → 9
    assert chapter.status == "pending_review"
    assert extract.calls == 1  # 干净路径照走抽取
    assert issues == []
    # 回炉草稿 prompt 里带 blocker 拼成的修订指令
    draft_prompts = [p for p in adapter.prompts if "现在开始写" in p]
    assert len(draft_prompts) == 2
    assert "一致性矛盾" in draft_prompts[1]
    assert "刚入睡" in draft_prompts[1]


def test_gate_quarantines_after_revision_cap():
    """回炉封顶仍有 blocker → quarantined:不抽圣经/不更新摘要/不提契约,issues 落 open。"""
    from app.db.models import ChapterIssue, ChapterState, ChapterSummary

    db, project, _ch1 = _make_db(review_max_revisions=1)
    check = _AlwaysCheck([BLOCKER_ISSUE])  # 回炉多少轮都是 blocker
    extract = _SpyExtract()
    _adapter, (chapter, issues, stats, _guard, review, _pf) = _run_generate(
        db, project, 2, check, extract
    )

    assert review["passed"] is False
    assert review["scores"]["continuity"] == 4  # blocker → 4
    assert chapter.status == "quarantined"
    assert extract.calls == 0, "quarantined 绝不能抽取进圣经"
    assert stats == {}
    # 滚动摘要没写、契约没提
    assert db.query(ChapterSummary).filter(
        ChapterSummary.project_id == project.id, ChapterSummary.chapter_number == 2
    ).first() is None
    assert db.query(ChapterState).filter(ChapterState.chapter_id == chapter.id).first() is None
    # issues 落库 open,severity 归一为 blocker
    rows = db.query(ChapterIssue).filter(ChapterIssue.chapter_id == chapter.id).all()
    assert len(rows) == 1
    assert rows[0].status == "open" and rows[0].severity == "blocker"
    assert rows[0].source == "gate"
    # 返回给 API 的问题列表即门禁结果
    assert issues and issues[0]["severity"] == "blocker"


def test_gate_quarantines_immediately_when_auto_revise_off():
    """关了自动修订:有 blocker 不回炉,直接隔离。"""
    db, project, _ch1 = _make_db(review_auto_revise=False, review_max_revisions=3)
    check = _ScriptedCheck([[BLOCKER_ISSUE]])
    extract = _SpyExtract()
    _adapter, (chapter, _i, _s, _g, review, _pf) = _run_generate(db, project, 2, check, extract)

    assert review["revision_rounds"] == 0
    assert chapter.status == "quarantined"
    assert extract.calls == 0


def test_gate_major_only_still_finalizes():
    """major(非 blocker):continuity=6 不达阈值 → 回炉;封顶后无 blocker 仍 pending_review。"""
    major = dict(BLOCKER_ISSUE, severity="major")
    db, project, _ch1 = _make_db(review_max_revisions=0)
    check = _ScriptedCheck([[major]])
    extract = _SpyExtract()
    _adapter, (chapter, issues, _s, _g, review, _pf) = _run_generate(db, project, 2, check, extract)

    assert review["scores"]["continuity"] == 6
    assert review["passed"] is False  # 五维判定不达阈值,但接受当前版本
    assert chapter.status == "pending_review"  # major 不隔离
    assert extract.calls == 1
    assert issues[0]["severity"] == "major"


# ---------- 分级回炉:门禁先行 + 定点修复(docs/08 §5.4) ----------

class _PatchAdapter:
    """定点修复用例的假 LLM:定稿返回非周期文本,替换锚可全篇唯一。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        if "现在开始写" in prompt:
            return "草稿正文。"
        if "修订后的" in prompt:
            return "沈墨在破庙里醒来。篝火只剩一点余烬。"
        if "场记" in prompt:
            return CONTRACT_JSON
        return "前情摘要。"


TIME_FIX = {
    "issue_index": 0,
    "original": "篝火只剩一点余烬。",
    "replacement": "不知过了多久,天将破晓,篝火只剩一点余烬。",
}


def test_gate_patches_blocker_then_passes():
    """blocker 可定点修:一次小调用修掉 → 门禁复查通过,不重写(草稿只调一次)。"""
    db, project, _ch1 = _make_db(review_max_revisions=3)
    check = _ScriptedCheck([[BLOCKER_ISSUE], []])
    extract = _SpyExtract()
    repair, repair_state = _scripted_repair([[TIME_FIX]])
    proofread, proof_state = _counting(_fake_proofread)
    review_fn, review_state = _counting(_fake_review_high)
    adapter = _PatchAdapter()
    from app.engines.pipeline import chapter as ch_mod

    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check),
        patch.object(ch_mod, "extract_and_apply", new=extract),
        patch.object(ch_mod, "proofread_chapter", new=proofread),
        patch.object(ch_mod, "review_chapter", new=review_fn),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
        patch.object(ch_mod, "repair_chapter", new=repair),
    ):
        _chapter, _issues, _stats, _guard, review, _pf = asyncio.run(
            ch_mod.generate_chapter(db, project, 2)
        )

    assert repair_state["calls"] == 1
    # 修复收到的是定稿原文;门禁复查收到的是修复后的正文
    assert repair_state["texts"][0].startswith("沈墨在破庙里醒来。")
    assert "天将破晓" in check.calls[1]
    # 精修(校对+主审)只在门禁干净后跑了一次,脏轮次没跑
    assert proof_state["calls"] == 1
    assert review_state["calls"] == 1
    # 没有重写:草稿 prompt 只出现一次
    assert len([p for p in adapter.prompts if "现在开始写" in p]) == 1
    assert review["passed"] is True
    assert review["revision_rounds"] == 1
    assert review["repair_rounds"] == 1
    assert len(review["repairs"]["applied"]) == 1
    assert _chapter.status == "pending_review"
    assert extract.calls == 1


def test_gate_patch_miss_falls_back_to_rewrite():
    """定点修复后仍有 blocker:下一轮强制重写,不再连续 patch(防烧轮)。"""
    db, project, _ch1 = _make_db(review_max_revisions=3)
    check = _ScriptedCheck([[BLOCKER_ISSUE], [BLOCKER_ISSUE], []])
    extract = _SpyExtract()
    repair, repair_state = _scripted_repair([[TIME_FIX], []])
    adapter = _PatchAdapter()
    from app.engines.pipeline import chapter as ch_mod

    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check),
        patch.object(ch_mod, "extract_and_apply", new=extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
        patch.object(ch_mod, "repair_chapter", new=repair),
    ):
        _chapter, _issues, _stats, _guard, review, _pf = asyncio.run(
            ch_mod.generate_chapter(db, project, 2)
        )

    assert repair_state["calls"] == 1  # 只 patch 一次
    assert len([p for p in adapter.prompts if "现在开始写" in p]) == 2  # 转重写
    assert review["revision_rounds"] == 2
    assert review["repair_rounds"] == 1
    assert review["passed"] is True
    assert _chapter.status == "pending_review"


def test_gate_unpatchable_blocker_rewrites_directly():
    """证据缺失的问题无法定位 → 分诊直接重写,不浪费修复调用。"""
    no_ev = dict(BLOCKER_ISSUE, evidence="")
    db, project, _ch1 = _make_db(review_max_revisions=3)
    check = _ScriptedCheck([[no_ev], []])
    extract = _SpyExtract()
    repair, repair_state = _scripted_repair([[TIME_FIX]])
    adapter = _PatchAdapter()
    from app.engines.pipeline import chapter as ch_mod

    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check),
        patch.object(ch_mod, "extract_and_apply", new=extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
        patch.object(ch_mod, "repair_chapter", new=repair),
    ):
        _chapter, _issues, _stats, _guard, review, _pf = asyncio.run(
            ch_mod.generate_chapter(db, project, 2)
        )

    assert repair_state["calls"] == 0
    assert len([p for p in adapter.prompts if "现在开始写" in p]) == 2
    assert review["revision_rounds"] == 1
    assert review["repair_rounds"] == 0
    assert review["passed"] is True


def test_triage_and_apply_gate_fixes():
    """分诊规则 + 定点替换的唯一锚校验(纯函数)。"""
    from app.engines.consistency.checker import _normalize_issue, triage_issues
    from app.engines.editorial import apply_gate_fixes

    # 分诊:有证据且未标 rewrite → patch(缺省乐观);缺证据/标 rewrite → 重写
    assert triage_issues([dict(BLOCKER_ISSUE)]) == "patch"
    assert triage_issues([dict(BLOCKER_ISSUE, fix_mode="patch")]) == "patch"
    assert triage_issues([dict(BLOCKER_ISSUE, evidence="")]) == "rewrite"
    assert triage_issues([dict(BLOCKER_ISSUE, fix_mode="rewrite")]) == "rewrite"
    assert triage_issues([
        dict(BLOCKER_ISSUE), dict(BLOCKER_ISSUE, fix_mode="rewrite"),
    ]) == "rewrite"
    # 归一:fix_mode 缺失/非法 → patch
    text = "沈墨在破庙里醒来。"
    assert _normalize_issue({"description": "x", "evidence": "沈墨在破庙里醒来"}, text)["fix_mode"] == "patch"
    assert _normalize_issue({"description": "x", "fix_mode": "rewrite"}, text)["fix_mode"] == "rewrite"
    assert _normalize_issue({"description": "x", "fix_mode": "WILD"}, text)["fix_mode"] == "patch"

    # 应用:唯一锚应用;不唯一/找不到/无效 → failed,不误伤
    content = "山上有个庙。庙里有个老和尚。山上有个庙。"
    new, applied, failed = apply_gate_fixes(content, [
        {"original": "庙里有个老和尚", "replacement": "庙里有个小和尚"},
        {"original": "山上有个庙", "replacement": "山下有个庙"},
        {"original": "不存在", "replacement": "x"},
        {"original": "同文", "replacement": "同文"},
    ])
    assert "小和尚" in new and "老和尚" not in new
    assert len(applied) == 1
    assert {f["reason"] for f in failed} == {
        "片段不唯一,拒绝误伤", "正文中找不到该片段", "无效修复项",
    }


def test_rewrite_clears_quarantine():
    """出口一:重写该章,门禁通过 → 状态回 pending_review,旧 open issues 清账。"""
    from app.db.models import ChapterIssue

    db, project, _ch1 = _make_db(review_max_revisions=0)
    check = _ScriptedCheck([[BLOCKER_ISSUE]])
    _a, (chapter, _i, _s, _g, _r, _pf) = _run_generate(
        db, project, 2, check, _SpyExtract()
    )
    assert chapter.status == "quarantined"
    assert db.query(ChapterIssue).filter(ChapterIssue.chapter_id == chapter.id).count() == 1

    # 重写(同章再生成),这次门禁干净
    check2 = _ScriptedCheck([[]])
    extract2 = _SpyExtract()
    _a2, (chapter2, issues2, _s2, _g2, _r2, _pf2) = _run_generate(
        db, project, 2, check2, extract2
    )
    assert chapter2.status == "pending_review"
    assert issues2 == []
    assert extract2.calls == 1
    # 旧 open 被 purge,本章无遗留 open issues
    assert db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == chapter2.id, ChapterIssue.status == "open"
    ).count() == 0


# ---------- API 层:连写队列停止 + 放行端点 ----------
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时未完成: {job}"
        time.sleep(0.02)


def _seed_quarantine_book(username: str, client: TestClient):
    """直接落库:一个项目 + 两章大纲 + 第 1 章正文/契约。返回 (headers, project_id)。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    headers = _auth(client, username)
    r = client.post("/api/projects", headers=headers,
                    json={"title": f"放行测试书-{username}", "target_chapters": 2})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    session = SessionLocal()
    try:
        for n, title in ((1, "破庙夜宿"), (2, "破庙清晨")):
            session.add(Outline(
                project_id=pid, chapter_number=n, title=title,
                chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
            ))
        ch1 = Chapter(
            project_id=pid, chapter_number=1, final_content=CH1_TEXT,
            word_count=len(CH1_TEXT), status="approved",
        )
        session.add(ch1)
        session.flush()
        session.add(ChapterState(
            chapter_id=ch1.id, contract=CONTRACT_JSON,
            content_hash=content_hash(CH1_TEXT), extract_status="ok",
        ))
        session.commit()
    finally:
        session.close()
    return headers, pid


def _db_rows(pid: int):
    from app.db.session import SessionLocal
    from app.db.models import Chapter, ChapterIssue, ChapterState, ChapterSummary

    session = SessionLocal()
    try:
        chapters = session.query(Chapter).filter(Chapter.project_id == pid).all()
        out = {"chapters": {c.chapter_number: c for c in chapters}, "issues": {}, "summaries": {}, "states": {}}
        for c in chapters:
            out["issues"][c.chapter_number] = session.query(ChapterIssue).filter(
                ChapterIssue.chapter_id == c.id).all()
            out["summaries"][c.chapter_number] = session.query(ChapterSummary).filter(
                ChapterSummary.project_id == pid,
                ChapterSummary.chapter_number == c.chapter_number).first()
            out["states"][c.chapter_number] = session.query(ChapterState).filter(
                ChapterState.chapter_id == c.id).first()
        return out
    finally:
        session.close()


def _chapter_patches(check_fn, extract_fn):
    """generate_chapter 的 LLM 依赖统一 mock(走 API 时同样生效:patch 的是定义模块)。"""
    from app.engines.pipeline import chapter as ch_mod

    adapter = _PipelineAdapter()
    return adapter, (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check_fn),
        patch.object(ch_mod, "extract_and_apply", new=extract_fn),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
    )


def test_queue_stops_on_quarantined(client):
    """连写队列:第 2 章被门禁拦截 → job 报错即停,后续章不生成。"""
    headers, pid = _seed_quarantine_book("gate_queue_user", client)
    check = _AlwaysCheck([BLOCKER_ISSUE])  # 默认 auto_revise 回炉封顶后仍是 blocker

    _adapter, patches = _chapter_patches(check, _SpyExtract())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/generate-queue",
            headers=headers, json={"chapter_numbers": [2]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error"
    assert "quarantined" in job["error"]
    assert "一致性门禁" in job["error"]
    rows = _db_rows(pid)
    assert rows["chapters"][2].status == "quarantined"  # 落库但隔离
    assert rows["summaries"][2] is None  # 未更新摘要


def test_gate_release_endpoint(client):
    """放行端点:open issues 标 ignored + 状态回 pending_review + 补走抽取/摘要/契约。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    headers, pid = _seed_quarantine_book("gate_release_user", client)
    check = _AlwaysCheck([BLOCKER_ISSUE])  # 默认 auto_revise 回炉封顶后仍隔离
    extract = _SpyExtract()
    _adapter, patches = _chapter_patches(check, extract)

    # 先让第 2 章被隔离(走同步生成端点)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/2/generate", headers=headers, json={}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "quarantined"
        assert body["gate"]["status"] == "quarantined"
        assert len(body["gate"]["blockers"]) == 1
        assert body["consistency_issues"][0]["severity"] == "blocker"
        assert body["review"]["scores"]["continuity"] == 4

    # 非隔离章调用放行 → 400
    r = client.post(f"/api/projects/{pid}/chapters/1/gate-release", headers=headers)
    assert r.status_code == 400

    # 放行第 2 章:补走章后链路(抽取哨兵清零重计)
    extract.calls = 0
    _adapter2, patches2 = _chapter_patches(_ScriptedCheck([[]]), extract)
    with patches2[0], patches2[1], patches2[2], patches2[3], patches2[4], patches2[5]:
        r = client.post(f"/api/projects/{pid}/chapters/2/gate-release", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["status"] == "pending_review"
    assert extract.calls == 1, "放行必须补走章后抽取"

    rows = _db_rows(pid)
    ch2 = rows["chapters"][2]
    assert ch2.status == "pending_review"
    # open issues 全部转 ignored
    assert rows["issues"][2] and all(i.status == "ignored" for i in rows["issues"][2])
    # 补走了滚动摘要与章末契约
    assert rows["summaries"][2] is not None
    assert rows["states"][2] is not None and rows["states"][2].extract_status == "ok"
    # issues 查询端点可见
    r = client.get(f"/api/projects/{pid}/chapters/2/issues", headers=headers)
    assert r.status_code == 200
    assert r.json()[0]["status"] == "ignored"


# ---------- API 层:单条问题定点修复(spot-repair,分级回炉手动入口) ----------

async def _fake_spot_recheck_clean(*a, **k):
    return []


async def _fake_spot_recheck_blocker(*a, **k):
    return [dict(BLOCKER_ISSUE)]


DAWN_FIX = {
    "issue_index": 0,
    "original": "篝火只剩一点余烬。",
    "replacement": "不知过了多久,天将破晓,篝火只剩一点余烬。",
}


def _seed_repairable_issue(username: str, client, *, evidence: str = "篝火只剩一点余烬。"):
    """直接落库:一个项目 + 第 2 章正文与 open gate issue。返回 (headers, pid, issue_id)。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter, ChapterIssue, Outline
    from app.engines.editorial import content_hash

    headers = _auth(client, username)
    r = client.post("/api/projects", headers=headers,
                    json={"title": f"定点修复测试书-{username}", "target_chapters": 2})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    text = "沈墨在破庙里醒来。篝火只剩一点余烬。"
    session = SessionLocal()
    try:
        session.add(Outline(
            project_id=pid, chapter_number=2, title="破庙清晨",
            chapter_purpose="推进主线", summary="第2章剧情", current_version=1,
        ))
        ch = Chapter(project_id=pid, chapter_number=2, final_content=text,
                     word_count=len(text), status="quarantined")
        session.add(ch)
        session.flush()
        row = ChapterIssue(
            chapter_id=ch.id, source="gate", severity="blocker", issue_type="state",
            description="上章末刚入睡,本章却清醒活动,无时间跳跃交代",
            evidence=evidence, suggestion="开头补一段时间流逝的交代",
            status="open", content_hash=content_hash(text),
        )
        session.add(row)
        session.commit()
        return headers, pid, row.id
    finally:
        session.close()


def test_spot_repair_success(client):
    """定点修复:锚唯一命中 + 复查干净 → 正文更新、issue resolved、留版本快照。"""
    from app.api.chapters import issues as issues_mod

    headers, pid, issue_id = _seed_repairable_issue("spot_repair_ok_user", client)

    async def _fake_repair(*a, **k):
        return [dict(DAWN_FIX)]

    with (
        patch.object(issues_mod, "repair_chapter", new=_fake_repair),
        patch.object(issues_mod, "check_chapter", new=_fake_spot_recheck_clean),
    ):
        r = client.post(
            f"/api/projects/{pid}/chapters/2/issues/{issue_id}/spot-repair",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["ok"] is True
    assert len(job["result"]["applied"]) == 1

    rows = _db_rows(pid)
    ch2 = rows["chapters"][2]
    assert "天将破晓" in ch2.final_content
    assert ch2.word_count == len(ch2.final_content)
    issue_row = [i for i in rows["issues"][2] if i.id == issue_id][0]
    assert issue_row.status == "resolved"
    # 修复前正文留了版本快照,可回滚
    from app.db.session import SessionLocal
    from app.db.models import ChapterVersion

    session = SessionLocal()
    try:
        versions = session.query(ChapterVersion).filter(
            ChapterVersion.chapter_id == ch2.id).all()
    finally:
        session.close()
    assert len(versions) == 1 and versions[0].source == "spot_repair"
    assert "天将破晓" not in versions[0].final_content


def test_spot_repair_recheck_blocker_keeps_text_open(client):
    """复查仍有 blocker:一字不落,issue 保持 open,结果说明原因。"""
    from app.api.chapters import issues as issues_mod

    headers, pid, issue_id = _seed_repairable_issue("spot_repair_blocker_user", client)

    async def _fake_repair(*a, **k):
        return [dict(DAWN_FIX)]

    with (
        patch.object(issues_mod, "repair_chapter", new=_fake_repair),
        patch.object(issues_mod, "check_chapter", new=_fake_spot_recheck_blocker),
    ):
        r = client.post(
            f"/api/projects/{pid}/chapters/2/issues/{issue_id}/spot-repair",
            headers=headers,
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["ok"] is False
    assert "按建议修订" in job["result"]["reason"]
    rows = _db_rows(pid)
    assert "天将破晓" not in rows["chapters"][2].final_content
    issue_row = [i for i in rows["issues"][2] if i.id == issue_id][0]
    assert issue_row.status == "open"


def test_spot_repair_anchor_miss_noop(client):
    """锚在正文中定位不到:修复一律不应用,正文与 issue 状态都不变。"""
    from app.api.chapters import issues as issues_mod

    headers, pid, issue_id = _seed_repairable_issue("spot_repair_miss_user", client)

    async def _fake_repair(*a, **k):
        return [{"issue_index": 0, "original": "正文里没有这句话。", "replacement": "x"}]

    with (
        patch.object(issues_mod, "repair_chapter", new=_fake_repair),
        patch.object(issues_mod, "check_chapter", new=_fake_spot_recheck_clean),
    ):
        r = client.post(
            f"/api/projects/{pid}/chapters/2/issues/{issue_id}/spot-repair",
            headers=headers,
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["ok"] is False
    assert "定位不到" in job["result"]["reason"]
    rows = _db_rows(pid)
    assert "天将破晓" not in rows["chapters"][2].final_content
    issue_row = [i for i in rows["issues"][2] if i.id == issue_id][0]
    assert issue_row.status == "open"


def test_spot_repair_rejects_issue_without_evidence(client):
    """没有逐字证据的问题无法定点:400 拒绝,不建任务。"""
    headers, pid, issue_id = _seed_repairable_issue(
        "spot_repair_noev_user", client, evidence="")

    r = client.post(
        f"/api/projects/{pid}/chapters/2/issues/{issue_id}/spot-repair",
        headers=headers,
    )
    assert r.status_code == 400
    assert "逐字证据" in r.json()["detail"]
