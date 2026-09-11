# tests/test_rework_branches.py
# -*- coding: utf-8 -*-
"""回炉段(`chapter_rework.review_and_rework`)的分支测试 —— 专钉「没被钉住的那几条」。

为什么单独一个文件:函数本身有端到端测试(test_continuity_gate 驱动 generate_chapter),
但覆盖率一量就发现,**两条最该钉死的分支反而没测到**:

- **门禁降级**(`chapter_rework.py:188-202`)——模型超时/输出解析失败时,
  「没有 blocker」不等于「检查通过」。过去这里 `return []`,模型一抽风安全网就
  自动撤掉,坏章当干净章落库。这是 P0 静默降级那批的最后一个口子。
- **主审降级**(`291-300`)——四维被打成 0 不是「写得差」,是「没解析出来」;
  为它回炉重写等于白烧钱。

端到端测试抓不到这两条,是因为它们要么需要 LLM 恰好在某一步返回脏数据、
要么需要精确的轮次脚本。这里**直接调 `review_and_rework`**(它是纯编排:
外部依赖全在 `_check/_repair/_review/_proofread` 四个 seam 上),按轮次喂脚本。

顺带钉住两条容易在重构中悄悄走样的:
- **`stalled_dims.discard`**(`322-323`)——某一维比上一轮**有改善**时必须重新
  给机会,否则「prose 6→7」也会被当成停滞而放弃;
- **校对报告只回显真正改上去的那几条**(`278-283`)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401
from app.db.base import Base

HIGH = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}


def _project(**project_kwargs):
    """隔离内存库 + 一个项目 + 第 2 章大纲(回炉段只读 project/outline 的字段)。"""
    from app.db.models import Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(
        title="回炉分支测试书", target_chapters=2, target_words_per_chapter=3000,
        **project_kwargs,
    )
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=2, title="渡口清晨",
        chapter_purpose="推进主线", summary="第2章剧情", current_version=1,
    ))
    db.commit()
    return db, project


class _Scripted:
    """按脚本依次返回结果;记录收到的正文(和 test_continuity_gate 同一套写法)。"""

    def __init__(self, seq: list):
        self._seq = list(seq)
        self.calls: list = []

    async def __call__(self, *a, **k):
        self.calls.append(a[3] if len(a) > 3 else "")
        return self._seq.pop(0) if self._seq else []


class _ScriptedReview:
    def __init__(self, seq: list):
        self._seq = list(seq)
        self.calls = 0

    async def __call__(self, *a, **k):
        self.calls += 1
        return self._seq.pop(0)


async def _clean_check(*a, **k):
    return []


async def _no_issues(*a, **k):
    return {"issues": []}


async def _no_fixes(*a, **k):
    return []


def _review_high(**scores):
    base = dict(HIGH)
    base.update(scores)
    return {"scores": base, "comment": "", "suggestions": []}


class _Compose:
    """假 compose:每次重写返回新一版正文,并记录轮次标签。"""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, rev_block, draft_label, finalize_label):
        self.calls.append(finalize_label)
        return ("重写草稿", "重写定稿")


def _rework(
    db, project, *,
    check=None, review=None, proofread=None, repair=None, compose=None,
    report=None, draft="首版草稿", final="首版定稿。", n=2,
):
    """直接跑一遍回炉段(四个外部依赖全在 seam 上,不碰 LLM/DB 网络)。"""
    from app.db.models import Outline
    from app.engines.pipeline import chapter_rework as rw_mod

    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number == n)
        .first()
    )
    compose = compose or _Compose()
    with (
        patch.object(rw_mod, "_check", new=check or _clean_check),
        patch.object(rw_mod, "_repair", new=repair or _no_fixes),
        patch.object(rw_mod, "_proofread", new=proofread or _no_issues),
        patch.object(rw_mod, "_review", new=review or (_stub_review_high())),
    ):
        outcome = asyncio.run(rw_mod.review_and_rework(
            db, project, n, draft, final,
            outline=outline, rolling="前情摘要", compose=compose, report=report,
        ))
    return outcome, compose


def _stub_review_high():
    async def _inner(*a, **k):
        return _review_high()

    return _inner


# =============== ① 门禁降级:没检查成 ≠ 检查通过 ===============

def test_gate_degraded_quarantines_and_burns_no_revision_round():
    """门禁降级 → 显式隔离,且**不烧回炉轮**(重跑解决不了模型抽风,只会白烧钱)。"""
    from app.engines.common import degraded_issue, is_degraded

    db, project = _project(review_max_revisions=3)
    check = _Scripted([[degraded_issue("consistency", "上游 502")]])

    outcome, compose = _rework(db, project, check=check)

    review = outcome.state.review_result
    assert review["passed"] is False
    assert "未能完成" in review["gate_note"]      # 有可见文案,不是静默放行
    assert review["revision_rounds"] == 0          # 一轮都没烧
    assert review["rework_log"][0]["trigger"] == "gate_degraded"
    assert is_degraded(outcome.gate_issues)        # 哨兵带出去,收尾阶段据此隔离
    assert compose.calls == []                     # 没有触发任何重写


def test_gate_degraded_beats_blocker_triage():
    """降级与 blocker 同时出现时,先判降级:连"问题清单"本身都不可信,分诊无从谈起。"""
    from app.engines.common import degraded_issue

    db, project = _project(review_max_revisions=3)
    blocker = {"severity": "blocker", "type": "state", "description": "刀伤位置矛盾",
               "evidence": "左臂", "suggestion": "改为右臂"}
    check = _Scripted([[degraded_issue("consistency", "解析失败"), blocker]])
    repair = _no_fixes

    outcome, compose = _rework(db, project, check=check, repair=repair)

    assert outcome.state.review_result["rework_log"][0]["trigger"] == "gate_degraded"
    assert compose.calls == []


# =============== ② 主审降级:没审成 ≠ 写得差 ===============

def test_review_degraded_quarantines_without_rewriting():
    """主审降级(四维被打成 0 是"没解析出来")→ 隔离待人工复核,不回炉。"""
    db, project = _project(review_max_revisions=3)
    degraded = dict(_review_high(plot=0, prose=0, pacing=0, character=0),
                    degraded=True, degraded_reason="输出不是 JSON")

    outcome, compose = _rework(db, project, review=_ScriptedReview([degraded]))

    review = outcome.state.review_result
    assert review["passed"] is False
    assert "未能完成" in review["review_note"]
    assert review["revision_rounds"] == 0
    assert outcome.state.review_degraded is True
    assert compose.calls == []


# =============== ③ 有改善就再给一轮 ===============

def test_improving_dim_is_not_treated_as_stalled():
    """prose 5→5 判停滞,5→6 必须**撤销**停滞判定(否则"有改善"也会被放弃)。

    脚本(r1~r4):(plot5,prose5) → (plot6,prose5) → (plot7,prose6) → 全 9。
    r2 时 prose 原地踏步被记为停滞;r3 时 prose 涨到 6,若不再给机会,
    retryable 会变空 → 直接以「连续无改善」收工,第 4 轮压根到不了。
    """
    db, project = _project(review_max_revisions=3, review_pass_threshold=7)
    review = _ScriptedReview([
        _review_high(plot=5, prose=5),
        _review_high(plot=6, prose=5),
        _review_high(plot=7, prose=6),
        _review_high(),
    ])

    outcome, compose = _rework(db, project, review=review)

    assert review.calls == 4
    assert outcome.state.review_result["passed"] is True
    assert outcome.state.revision_rounds == 3
    assert len(compose.calls) == 3
    # 没有任何维度被报成「连续无改善」——prose 的停滞判定已被撤销
    assert "hints" not in outcome.state.review_result


# =============== ④ 校对报告只记真正改上去的 ===============

def test_proofread_report_keeps_only_applied_fixes():
    """正文里找不到的校对项不进回显清单:报告说"修了 N 处"就必须真有 N 处被改上去。"""
    async def _proofread(text):
        return {"issues": [
            {"type": "typo", "original": "睁开了眼", "suggestion": "睁开眼", "reason": "赘字"},
            {"type": "typo", "original": "查无此句", "suggestion": "x", "reason": "脏"},
        ]}

    db, project = _project(review_max_revisions=0)
    outcome, _compose = _rework(
        db, project, proofread=_proofread, final="沈墨在天光里睁开了眼。",
    )

    state = outcome.state
    assert state.proofread_fixed == 1
    assert [it["original"] for it in state.last_fixed_issues] == ["睁开了眼"]
    assert outcome.final == "沈墨在天光里睁开了眼。".replace("睁开了眼", "睁开眼")
    assert outcome.state.review_result["proofread_fixed"] == 1


# =============== ⑤ 两个纯函数的小分支 ===============

def test_prose_directive_ignores_dirty_scores():
    """prose 取不到有效分(None/缺字段/脏值)时不注入禁则——禁则只该在它确实挂了时出现。"""
    from app.engines.pipeline.chapter_rework import (
        _PROSE_REWRITE_DIRECTIVE,
        _with_prose_directive,
    )

    assert _with_prose_directive("改一改", {}, 7) == "改一改"
    assert _with_prose_directive("改一改", {"prose": None}, 7) == "改一改"
    assert _with_prose_directive("改一改", {"prose": "六"}, 7) == "改一改"
    # 真有分:低于阈值才追加,达标原样返回
    assert _with_prose_directive("改一改", {"prose": 7}, 7) == "改一改"
    assert _with_prose_directive("改一改", {"prose": 5}, 7) == f"改一改;{_PROSE_REWRITE_DIRECTIVE}"
    assert _with_prose_directive("", {"prose": 5}, 7) == _PROSE_REWRITE_DIRECTIVE


def test_report_callback_failure_never_breaks_generation():
    """进度回调抛异常绝不能影响生成:上报是旁路,失败就丢,不往上冒。"""
    def _boom(stage: str) -> None:
        raise RuntimeError("进度通道断了")

    db, project = _project(review_max_revisions=0)
    outcome, _compose = _rework(db, project, report=_boom)

    assert outcome.state.review_result["passed"] is True
