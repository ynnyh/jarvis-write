# tests/test_chapter_decomposition.py
# -*- coding: utf-8 -*-
"""generate_chapter 四阶段拆解的结构守卫(阶段一·A)。

拆解本身若只靠「全量测试还是绿的」来验收,是不够的:历史上就出现过
拆引用时留下 UnboundLocalError、而 1300 条测试全绿的事故(那条路径没人走)。
所以本文件专门钉住三件事:

1. **模块边界**:四个阶段的入口确实各自成模块,`generate_chapter` 只编排;
2. **兼容再导出**:老调用方从 chapter.py 导入的那些下划线名字必须还在
   (api/chapters/*、diagnosis、outline_discuss 都依赖它们);
3. **条件分支可达**:每个阶段的可注入 seam 都能被换掉并影响结果——
   即「拆出来的模块真的在链路上」,不是死代码。
"""
from __future__ import annotations

import asyncio

import pytest

from app.engines.pipeline import (
    chapter as ch_mod,
    chapter_compose as cc_mod,
    chapter_finalize as cf_mod,
    chapter_rework as rw_mod,
)


# ---------- 1. 模块边界 ----------

def test_generate_chapter_delegates_to_four_stage_modules():
    """generate_chapter 的源码里应当只出现四阶段的调用,而不是内联长逻辑。"""
    import inspect

    src = inspect.getsource(ch_mod.generate_chapter)
    lines = src.splitlines()
    assert len(lines) < 260, (
        f"generate_chapter 又长回去了({len(lines)} 行);编排器不该超过 ~260 行"
    )
    # 四个阶段的入口都必须在源码里显式出现(漏一个 = 有人把逻辑抄回来了)
    assert "_prepare_chapter_context(" in src
    assert "Composer(" in src
    assert "review_and_rework(" in src
    assert "finalize_and_persist(" in src


def test_stage_modules_own_their_responsibilities():
    """各阶段的标志性符号必须落在自己的模块里(职责没被搬回去)。"""
    # 阶段 2:草稿/定稿 prompt 组装
    assert hasattr(cc_mod, "Composer") and hasattr(cc_mod, "ChapterContext")
    # 阶段 3:回炉循环 + 三条退出路径依赖的判断
    assert hasattr(rw_mod, "review_and_rework")
    # 阶段 4:收尾 + 隔离
    assert hasattr(cf_mod, "finalize_and_persist") and hasattr(cf_mod, "FinalizeResult")


# ---------- 2. 兼容再导出(老调用方不能断) ----------

@pytest.mark.parametrize(
    "name",
    [
        # api/chapters/*、diagnosis、outline_discuss 直接从这里导入
        "_strip_meta",
        "_beats_block",
        "_drama_task_block",
        "_deai_rules_block",
        "_next_chapter_brief",
        "_revision_block",
        "_with_prose_directive",
        "_gate_merged_review",
        "_rolling_summary",
        "_recent_tail",
        "apply_chapter_tail",
        "rebuild_summaries_after",
        "update_style_memo",
        # 常量(测试与配置比对用)
        "_REVISION_EXCERPT_CHARS",
        "_DEAI_ESCALATE_HITS",
        "_PROSE_REWRITE_DIRECTIVE",
        "_DIM_CN",
    ],
)
def test_legacy_symbols_still_importable_from_chapter(name):
    assert hasattr(ch_mod, name), f"兼容再导出断了:{name}"


def test_reexports_are_the_same_objects():
    """再导出必须是同一对象,不能是复制品(否则测试 patch 一处、生产走另一处)。"""
    assert ch_mod._revision_block is rw_mod._revision_block
    assert ch_mod._with_prose_directive is rw_mod._with_prose_directive
    assert ch_mod._drama_task_block is cc_mod._drama_task_block


# ---------- 3. 条件分支可达(seam 真的在链路上) ----------

def test_rework_seams_are_module_level_and_patchable():
    """回炉段的四个 seam 都必须是模块级名字——patch 才有意义。"""
    for seam in ("_check", "_repair", "_review", "_proofread"):
        assert hasattr(rw_mod, seam), seam
    # 它们是默认实现,不是 None
    assert callable(rw_mod._check)
    assert callable(rw_mod._review)


def test_finalize_seam_is_module_level():
    assert callable(cf_mod._persist_issues)


def test_deai_report_flows_into_memo():
    """fin 结果里的 deai_report 必须是 FlavorReport(带 categories),不是分数。

    这个坑踩过:memo_notes_block 读 report.categories,只传分数会在章后链路
    崩 AttributeError,而门禁/回炉那一堆测试都测不到(它们短路在前面)。
    """
    from app.engines.polish import ai_flavor_report

    report = ai_flavor_report("他的眼中闪过一丝不易察觉的光芒。" * 3)
    assert hasattr(report, "categories") and hasattr(report, "score")
    # 分数是 float,报告是对象——两者都能从 healer 返回,别搞混
    fin_fields = cf_mod.FinalizeResult.__dataclass_fields__
    assert "deai_report" in fin_fields and "deai_after" in fin_fields


def test_prepare_context_carries_every_prompt_input():
    """PreparedContext 的字段必须覆盖 Composer 需要的一切。

    漏字段 = 某处 prompt 注入静默变空串(不会报错,只会写得更差)。
    """
    import dataclasses

    ctx_fields = {f.name for f in dataclasses.fields(ch_mod.PreparedContext)}
    cc_fields = {f.name for f in dataclasses.fields(cc_mod.ChapterContext)}
    # PreparedContext 里除 outline/next_outline/preflight_issues 外的字段,
    # 应当能在 ChapterContext 找到同名对应(compose_context 是逐字段搬运)。
    expected = {
        "outline", "next_outline", "style_block", "rolling", "recent",
        "handoff_block", "hard_constraints", "known_roster", "resource_ledger",
        "foreshadow_reminders", "device_reminders", "avoid_repetition",
        "twist_prep",
    }
    assert expected <= ctx_fields, expected - ctx_fields
    assert expected <= cc_fields, expected - cc_fields


def test_prepare_context_builds_every_field_it_declares():
    """_prepare_chapter_context 必须真的给每个声明字段赋值(无遗漏、无飘空)。

    这是「拆函数最容易踩的坑」的守卫:字段声明了但构造时漏传,
    dataclass 会取默认值(常是空串),静默降级——测试全绿,正文变差。
    只读源码即可判定:构造 PreparedContext(...) 的实参名集合 == 字段集合。
    """
    import inspect
    import re
    import dataclasses

    src = inspect.getsource(ch_mod._prepare_chapter_context)
    m = re.search(r"return PreparedContext\((.*?)\n    \)", src, re.S)
    assert m, "没找到 PreparedContext 构造"
    passed = set(re.findall(r"(\w+)\s*=", m.group(1)))
    declared = {f.name for f in dataclasses.fields(ch_mod.PreparedContext)}
    missing = declared - passed
    assert not missing, f"_prepare_chapter_context 漏传字段:{sorted(missing)}"
    extra = passed - declared
    assert not extra, f"传了 PreparedContext 没有的字段:{sorted(extra)}"


def test_compose_context_maps_every_field_without_collapse():
    """compose_context 逐字段搬运,且**不许塌缩成同一值**。

    反向验证过的写法:只断言"字段非空"是抓不到漏搬的——把每个字段都赋成
    同一个常量照样"非空"。所以这里给每个字段灌独一无二的哨兵值,再逐个
    比对;任何一个字段错位/漏搬/被写死,立刻红。
    """
    stub_outline = type("O", (), {"chapter_number": 7})()
    ctx = ch_mod.PreparedContext(
        outline=stub_outline,
        next_outline=None,
        style_block="STYLE",
        rolling="ROLL",
        recent="RECENT",
        recent_full=["a"],
        handoff_block="HANDOFF",
        hard_constraints="HARD",
        resource_ledger="LEDGER",
        known_roster="ROSTER",
        foreshadow_reminders="FORE",
        device_reminders="DEV",
        avoid_repetition="AVOID",
        twist_prep="TWIST",
        revision_block="REV",
        preflight_issues=[],
    )
    project = type("P", (), {"target_chapters": 10})()
    cc = ctx.compose_context(project=project)
    assert cc.chapter_number == 7
    # 逐字段哨兵比对:每对 (源字段, 目标字段) 都必须是"这个字段搬到了那个字段"
    for field in (
        "style_block", "rolling", "recent", "handoff_block", "hard_constraints",
        "resource_ledger", "known_roster", "foreshadow_reminders",
        "device_reminders", "avoid_repetition", "twist_prep",
    ):
        sentinel = getattr(ctx, field)
        assert getattr(cc, field) == sentinel, f"{field} 没搬到 ChapterContext"
        # 且不能被别的字段串了(串字段时值会等于另一个哨兵)
        others = {
            getattr(ctx, o) for o in (
                "style_block", "rolling", "recent", "handoff_block",
                "hard_constraints", "resource_ledger", "known_roster",
                "foreshadow_reminders", "device_reminders",
                "avoid_repetition", "twist_prep",
            ) if o != field
        }
        assert sentinel not in others, f"{field} 的哨兵值与别的字段撞了,测试失效"
    assert cc.outline is stub_outline
    assert cc.next_outline is None
    # deai_rules 由 recent_full 现算,不该是空串(即便没脏,也至少是核心版)
    assert cc.deai_rules
    assert cc.project is project


def test_composer_short_circuits_with_precomputed_without_rev_block():
    """场景级首轮已生成 → Composer 不该再调 LLM(precomputed 短路)。"""
    import dataclasses

    stub_outline = type("O", (), {"chapter_number": 1})()
    ctx = cc_mod.ChapterContext(
        chapter_number=1, outline=stub_outline, next_outline=None,
        style_block="", rolling="", recent="", handoff_block="",
        hard_constraints="", known_roster="", resource_ledger="",
        foreshadow_reminders="", device_reminders="", avoid_repetition="",
        twist_prep="", deai_rules="",
    )
    composer = cc_mod.Composer(ctx, precomputed=("D", "F"))
    d, f = asyncio.run(composer(""))
    assert (d, f) == ("D", "F")


# ---------- 4. 隔离原因文案(用户看的说法必须区分两类) ----------

def test_quarantine_note_distinguishes_gate_from_degraded():
    """「有矛盾」与「没校验成」行为一致但说法不同——测试钉住这个区分。"""
    calls: list[str] = []

    note_blocked = cf_mod._quarantine_note(
        3, [{"description": "x"}], [], False, calls.append
    )
    assert "硬矛盾" in note_blocked and "quarantined" in note_blocked

    note_gate = cf_mod._quarantine_note(3, [], [{"severity": "blocker"}], False, calls.append)
    assert "一致性检查未能完成" in note_gate

    note_review = cf_mod._quarantine_note(3, [], [], True, calls.append)
    assert "主审评分未能完成" in note_review

    assert len(calls) == 3  # 三次都上报了进度
