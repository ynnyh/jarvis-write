# tests/test_review_gate.py
# -*- coding: utf-8 -*-
"""生成时审校把关测试(mock LLM,无需 API key)。

两层:
1. 引擎纯函数单测:judge_passed 达标判定 / apply_proofread_fixes 精确替换 /
   build_revision_directive 意见拼装 / review_chapter & proofread_chapter 的
   分数钳制与幻觉过滤。
2. generate_chapter 审校把关回环集成测:达标即收 / 不达标带意见回炉 /
   回炉封顶 review_max_revisions(不会无限回炉)/ auto_revise 关闭则不回炉。

回环里 judge_passed / build_revision_directive / apply_proofread_fixes 用真函数
(纯逻辑,正是要验证的对象),只 patch 掉会调 LLM 的 review_chapter / proofread_chapter。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


class _FnAdapter:
    """按 prompt 内容返回回复的假 LLM;调用次数不限(审校回炉会多轮)。"""

    def __init__(self, reply_fn):
        self.reply_fn = reply_fn
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply_fn(len(self.prompts), prompt)


# ---------- judge_passed ----------
def test_judge_passed():
    from app.engines.editorial import judge_passed

    # 四维全 >= 阈值 → 达标
    assert judge_passed({"plot": 7, "prose": 7, "pacing": 7, "character": 7}, 7)
    assert judge_passed({"plot": 9, "prose": 8, "pacing": 10, "character": 7}, 7)
    # 任一维低于阈值 → 不达标
    assert not judge_passed({"plot": 7, "prose": 6, "pacing": 7, "character": 7}, 7)
    # 缺维度(0/None)视为不达标
    assert not judge_passed({"plot": 9, "prose": 9}, 7)
    assert not judge_passed({"plot": 9, "prose": 9, "pacing": None, "character": 9}, 7)
    # 阈值可调高:同样的分,8 分线就过不去
    assert not judge_passed({"plot": 7, "prose": 7, "pacing": 7, "character": 7}, 8)


# ---------- apply_proofread_fixes ----------
def test_apply_proofread_fixes():
    from app.engines.editorial import apply_proofread_fixes

    content = "他走进了房间,看见了她的脸。"
    issues = [
        {"original": "走进", "suggestion": "走入"},          # 正常替换
        {"original": "不存在的片段", "suggestion": "xxx"},   # 定位不到 → failed
        {"original": "她的脸", "suggestion": "她的脸"},      # 原文=建议 → 无效
    ]
    new, applied, failed = apply_proofread_fixes(content, issues)
    assert "走入" in new and "走进" not in new
    assert len(applied) == 1 and applied[0]["original"] == "走进"
    assert len(failed) == 2  # 找不到 + 无效各一


# ---------- build_revision_directive ----------
def test_build_revision_directive():
    from app.engines.editorial import build_revision_directive

    review = {
        "comment": "节奏偏慢",
        "suggestions": [
            {"evidence": "他慢慢走", "issue": "拖沓", "fix": "加快"},
            {"evidence": "", "issue": "对话太少", "fix": ""},
        ],
    }
    directive = build_revision_directive(review)
    assert "主编总评:节奏偏慢" in directive
    assert '"他慢慢走"这里:拖沓,改法:加快' in directive
    assert "对话太少" in directive
    assert len(directive) <= 500


# ---------- review_chapter:分数钳制 + 建议幻觉过滤 ----------
def test_review_chapter_clamps_and_filters():
    from app.engines import editorial as ed

    # 分数越界(15/-3)钳制到 1-10;建议举证不在正文里 → evidence 置空但建议保留
    raw = (
        '{"scores": {"plot": 15, "prose": -3, "pacing": 8, "character": 0}, '
        '"comment": " 总评 ", '
        '"suggestions": ['
        '{"evidence": "正文里没有这句", "issue": "问题A", "fix": "改A"}, '
        '{"evidence": "她的脸", "issue": "问题B", "fix": "改B"}'
        "]}"
    )

    class _Adapter:
        async def ask(self, prompt, system=None):
            return raw

    with patch.object(ed, "get_adapter_for", return_value=_Adapter()):
        result = asyncio.run(ed.review_chapter("正文里有她的脸这句", "标题:x"))

    assert result["scores"]["plot"] == 10      # 15 → 10
    assert result["scores"]["prose"] == 1      # -3 → 1
    assert result["scores"]["pacing"] == 8
    assert result["scores"]["character"] == 0  # 0 分保留(前端显示 —)
    assert result["comment"] == "总评"
    # 幻觉举证被清空,但建议本身保留;真实举证保留
    assert result["suggestions"][0]["evidence"] == ""
    assert result["suggestions"][0]["issue"] == "问题A"
    assert result["suggestions"][1]["evidence"] == "她的脸"


# ---------- proofread_chapter:幻觉/无效问题过滤 ----------
def test_proofread_chapter_filters():
    from app.engines import editorial as ed

    raw = (
        '{"issues": ['
        '{"type": "typo", "original": "走进", "suggestion": "走入", "reason": "r"}, '
        '{"type": "typo", "original": "不在正文", "suggestion": "x", "reason": ""}, '
        '{"type": "dup", "original": "她的脸", "suggestion": "她的脸", "reason": ""}'
        "]}"
    )

    class _Adapter:
        async def ask(self, prompt, system=None):
            return raw

    with patch.object(ed, "get_adapter_for", return_value=_Adapter()):
        result = asyncio.run(ed.proofread_chapter("他走进房间看见她的脸"))

    # 只留下能在正文定位且原文!=建议的那一条
    assert len(result["issues"]) == 1
    assert result["issues"][0]["original"] == "走进"


# ---------- generate_chapter 审校回环集成 ----------
def _make_db(threshold=7, auto_revise=True, max_revisions=3):
    """独立内存库:一个项目 + 第 1 章大纲,审校配置可调。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="审校测试书", target_chapters=1, target_words_per_chapter=3000,
        review_pass_threshold=threshold, review_auto_revise=auto_revise,
        review_max_revisions=max_revisions,
    )
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="雨夜",
        chapter_purpose="主角登场", summary="主角在雨夜登场",
        current_version=1,
    ))
    db.commit()
    return db, project


class _FakeMemory:
    def __init__(self, *a, **k): pass
    async def retrieve(self, *a, **k): return []
    async def add_chapter(self, *a, **k): return None


async def _fake_check(*a, **k):
    return []


async def _fake_extract(*a, **k):
    return {}


def _reply_fn(i, prompt):
    """按 prompt 内容返回草稿/定稿/摘要,调用次数不限(回炉会多轮)。"""
    if "现在开始写" in prompt:
        return "这是草稿正文。" * 30
    if "修订后的" in prompt:
        return "这是定稿正文。" * 30
    return "前情摘要:主角完成了第一步。"


class _ScriptedReview:
    """按脚本依次返回主审结果,记录被调次数;脚本耗尽后返回达标分(兜底防死循环)。"""

    def __init__(self, score_sequence: list[dict]):
        self._seq = list(score_sequence)
        self.calls = 0

    async def __call__(self, content, outline_block):
        self.calls += 1
        if self._seq:
            scores = self._seq.pop(0)
        else:
            scores = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}
        return {"scores": scores, "comment": "脚本意见", "suggestions": []}


async def _fake_proofread(content):
    return {"issues": []}


def _run(db, project, review_fake) -> dict:
    """mock 掉 LLM 调用跑一遍 generate_chapter,返回审校结果 dict。"""
    from app.engines.pipeline import chapter as ch_mod

    adapter = _FnAdapter(_reply_fn)
    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "ChapterMemory", _FakeMemory),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=review_fake),
    ):
        _chapter, _issues, _stats, _guard, review_result = asyncio.run(
            ch_mod.generate_chapter(db, project, 1)
        )
    return review_result


HIGH = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}
LOW = {"plot": 5, "prose": 5, "pacing": 5, "character": 5}


def test_gate_passes_immediately_when_above_threshold():
    db, project = _make_db(threshold=7)
    review = _ScriptedReview([HIGH])
    result = _run(db, project, review)
    assert result["passed"] is True
    assert result["revision_rounds"] == 0
    assert result["threshold"] == 7
    assert review.calls == 1  # 只审了一次就过


def test_gate_revises_once_then_passes():
    db, project = _make_db(threshold=7, max_revisions=3)
    review = _ScriptedReview([LOW, HIGH])  # 第一轮不达标,回炉后达标
    result = _run(db, project, review)
    assert result["passed"] is True
    assert result["revision_rounds"] == 1
    assert review.calls == 2


def test_gate_caps_at_max_revisions_no_infinite_loop():
    db, project = _make_db(threshold=7, max_revisions=2)
    review = _ScriptedReview([LOW, LOW, LOW, LOW])  # 一直不达标
    result = _run(db, project, review)
    assert result["passed"] is False
    assert result["revision_rounds"] == 2  # 封顶 2 轮就接受当前版本
    assert review.calls == 3               # 初审 1 + 回炉 2


def test_gate_no_revision_when_auto_revise_off():
    db, project = _make_db(threshold=7, auto_revise=False, max_revisions=3)
    review = _ScriptedReview([LOW])
    result = _run(db, project, review)
    assert result["passed"] is False
    assert result["revision_rounds"] == 0  # 关了自动回炉,不达标也不回炉
    assert review.calls == 1
