# app/engines/pipeline/chapter_finalize.py
# -*- coding: utf-8 -*-
"""章定稿收尾:字数守卫 → AI 味自愈 → 去重守卫 → 落库 + 问题持久化。

从 chapter.py 拆出。这一段是「文字加工 + 落库」,与前面的生成/回炉段落
(见 chapter_compose)、以及后面的章后链路(chapter_maintenance)
职责清晰可切。拆出的动机:generate_chapter 曾是 690 行单函数,读的人
分不清「哪几步会改正文、哪几步只会写库、哪一步在什么条件下短路返回」。

本模块不碰 LLM 的选型决策(守卫/自愈各自内部自管),只负责:
- 在正确的顺序上调用三道加工(顺序错会互相抵消,见下);
- 把结果落成 Chapter 行 + 两份快照(主审/校对)+ 问题表;
- 决定 status(quarantined / pending_review)并把隔离原因报给进度回调。

事务纪律:本模块内部每次 commit 的时机都被刻意固定(跨 await 前必须先
提交,否则写锁会被并发的用量记账卡成 SQLITE_BUSY)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterVersion, Outline, Project
from app.engines.common import degraded_of, is_degraded
from app.engines.consistency.checker import blockers_of, persist_issues
from app.engines.consistency.repetition import dedup_paragraphs
from app.engines.editorial import (
    store_proofread_snapshot,
    store_review_snapshot,
)
from app.engines.pipeline.word_guard import GuardResult, word_count_guard
from app.engines.polish.polisher import deai_self_heal

logger = logging.getLogger("jarvis-write.chapter")

# 可注入接线点:问题持久化(测试用它断言隔离分支落库了哪些 issue,不必真跑队列)
_persist_issues = persist_issues


@dataclass
class FinalizeResult:
    """收尾段的产出。final_text 是最终正文(与入参 text 可能是不同字符串)。"""

    chapter: Chapter
    guard_result: GuardResult
    final_text: str
    # 本轮门禁结果(回炉段传出),落 chapter_issues 与返回值用
    gate_issues: list[dict] = field(default_factory=list)
    quarantined: bool = False
    # 隔离原因文案(仅 quarantined 时非空,用于进度上报)
    quarantine_note: str = ""
    # 去味是否被采纳(采纳才有 pre_deai_final 快照);deai_report 是末检 FlavorReport
    # (供 memo_notes_block 做「病灶回流」,不能只传分数——类别分布在里面)。
    deai_before: float = 0.0
    deai_after: float = 0.0
    deai_report: Any = None


async def finalize_and_persist(
    db: Session,
    project: Project,
    chapter_number: int,
    outline: Outline,
    text: str,
    *,
    draft: str,
    review_result: dict,
    gate_issues: list[dict],
    review_degraded: bool,
    reviewed_text: str,
    last_fixed_issues: list[dict],
    style_block: str,
    preflight_issues: list[dict] | None = None,
    report=None,
) -> FinalizeResult:
    """字数守卫 → 去味自愈 → 去重 → 落库。

    text/draft/reviewed_text 三者语义不同,别合并:
      - text:回炉段交出的定稿(守卫可能整篇压缩/拆章);
      - draft:本轮的草稿(落 Chapter.draft_content,是历史留档不是产物);
      - reviewed_text:审校/门禁所对应的正文——字数守卫之后正文会变,
        快照指纹必须以「审校时的文本」为准,否则快照会立刻被判过期。

    返回 FinalizeResult;隔离与否只体现在 is_quarantined,本函数不短路
    (调用方拿到结果后自己决定是否走章后链路)。
    """

    def _report(stage: str) -> None:
        if report:
            try:
                report(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    final = text

    # ---- 字数守卫:超标压缩/拆章(只对审校后的最终定稿跑一次) ----
    guard_result = await word_count_guard(
        db, project, chapter_number, outline, final, style_block, report=_report
    )
    final = guard_result.final_text

    # ---- AI 味自愈闭环:定稿终版体检,超标则定向去味重写(带安全阀) ----
    # 摆在字数守卫之后(最后一道文字加工):守卫的压缩本身是又一次 LLM 重写,可能重新
    # 引入套话——把去味放最后,既能修守卫引入的 AI 腔,又不会被守卫回炉抵消。安全阀在
    # deai_self_heal 内:未降分/篇幅越界/空输出一律丢弃回退,绝不落一版比守卫后更差的
    # 正文;干净文本(score≤门槛)直接短路、不调 LLM。style_block 带正向锚+配对反例。
    _report("5/6 AI 味自愈")
    _heal_input = final  # 去味前正文(P4 自愈埋记录:采纳了重写就存版本快照)
    final, _deai_before, _deai_after = await deai_self_heal(
        final, style_block, progress=_report
    )
    # 采纳了去味重写:去味前正文留一版快照(source=deai,前端「放弃去味」回退用),
    # 分数变化透传 review.deai(生成结果卡展示)。dedup 只删不写,发生在其后。
    pre_deai_final: str | None = None
    if _deai_after.score < _deai_before.score:
        pre_deai_final = _heal_input
        review_result["deai"] = {
            "before": _deai_before.score, "after": _deai_after.score,
        }
        logger.info(
            "第 %d 章 AI 味自愈:%.1f → %.1f(去味前正文已存版本快照)",
            chapter_number, _deai_before.score, _deai_after.score,
        )

    # ---- 去重段落守卫:删掉模型复读出的整段重复(纯规则零成本,落库前末道加工) ----
    # 摆在所有 LLM 文字加工(定稿/回炉/守卫压缩/去味重写)之后:上游任一步都可能复读出
    # 重复段,这里统一兜底。只删不写,不会引入新问题;鲜有的有意呼应靠长度门槛豁免。
    final, _dup_removed = dedup_paragraphs(final)
    if _dup_removed:
        logger.info("第 %d 章去重:删掉 %d 个重复段落", chapter_number, _dup_removed)

    # ---- 落库 ----
    # 先结束生成期间一直开着的读事务:期间用量记录等已在别的连接提交,
    # 旧快照直接升级写锁会撞 SQLITE_BUSY;commit 后用新事务写入。
    db.commit()
    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if chapter is None:
        chapter = Chapter(
            project_id=project.id,
            outline_id=outline.id,
            chapter_number=chapter_number,
        )
        db.add(chapter)
    elif guard_result.action != "split":
        # 重写:覆盖前把当前正文存一版快照,供新旧对比与回滚。
        # 拆章分支例外:_split_chapter 已把第 N 章正文原子落成 part_a 并提交,
        # 此刻 chapter.final_content 已是 part_a,再快照只会存一版 part_a→part_a
        # 的无意义历史;且下面的赋值(final 也 = part_a)对拆章是幂等的。
        from app.chapter_versions import snapshot_chapter

        snapshot_chapter(db, chapter, source="generated")
    chapter.outline_id = outline.id
    chapter.draft_content = draft
    chapter.final_content = final
    chapter.word_count = len(final)
    chapter.outline_version_used = outline.current_version
    chapter.is_stale = False
    # 门禁判定(docs/08 §5.4.3):回炉封顶仍有 blocker → 落库但隔离(quarantined),
    # 不做章后抽取(矛盾不进圣经)、不更新滚动摘要、不提契约;
    # 无 blocker → pending_review(docs/08 §5.5 审核状态机,人工 approve 后 approved)。
    blockers = blockers_of(gate_issues)
    # 降级与「有硬矛盾」同等处理:都隔离、都不进圣经。差别只在给用户的说法
    # (未校验 vs 有矛盾)——行为必须一致,未校验的正文照样会污染真相库。
    _gate_blocked = bool(blockers) or is_degraded(gate_issues) or review_degraded
    chapter.status = "quarantined" if _gate_blocked else "pending_review"
    # 审校快照落库:编辑部打开时回显本次主审结果,免去用户再点一次「请主编审读」
    store_review_snapshot(chapter, review_result, "generation", reviewed_text)
    # 校对快照落库:回显生成时自动修复了哪些硬伤(指纹与主审一致,正文改动同步失效)
    store_proofread_snapshot(chapter, last_fixed_issues, "generation", reviewed_text)
    db.flush()
    # P4 自愈埋记录:去味前的正文在此存一版快照(source=deai)。挪到这里是因为
    # 新建章的 id 要 flush 后才有;不 commit,随下面的正文提交一起落。
    if pre_deai_final is not None:
        from app.chapter_versions import next_version_number

        db.add(ChapterVersion(
            chapter_id=chapter.id,
            version=next_version_number(db, chapter.id),
            draft_content=draft,
            final_content=pre_deai_final,
            word_count=len(pre_deai_final),
            source="deai",
        ))
    # 正文立刻提交:后面章后链路还有数分钟 LLM 调用,
    # 不能拿着写锁跨这些 await(会把并发写卡到超时),失败也不该丢正文。
    db.commit()
    # issues 落库:purge 本章旧 open 按当前结果重建(幂等);
    # 指纹已变的旧 ignored 清除(不再生效),未变的保留(用户已确认忽略)。
    _persist_issues(db, chapter, gate_issues, source="gate", text=final)
    # 写前审核警告同法落库(source="preflight"),与门禁问题同面板展示
    _persist_issues(db, chapter, preflight_issues or [], source="preflight", text=final)
    db.commit()

    result = FinalizeResult(
        chapter=chapter,
        guard_result=guard_result,
        final_text=final,
        gate_issues=gate_issues,
        quarantined=_gate_blocked,
        deai_before=_deai_before.score,
        deai_after=_deai_after.score,
        deai_report=_deai_after,
    )
    if _gate_blocked:
        result.quarantine_note = _quarantine_note(
            chapter_number, blockers, gate_issues, review_degraded, _report
        )
    return result


def _quarantine_note(
    chapter_number: int,
    blockers: list[dict],
    gate_issues: list[dict],
    review_degraded: bool,
    report,
) -> str:
    """隔离原因:上报进度 + 留日志;返回给调用方(供 API 回显)。

    区分两种隔离:校验降级(未校验)vs 有硬矛盾。行为一致,说法不同——
    用户看到「未校验」和「有矛盾」该做的事不一样。
    """
    if not blockers:
        _why = "主审评分未能完成" if review_degraded else "一致性检查未能完成"
        report(f"{_why}:本章已隔离(quarantined),待人工复核")
        logger.warning(
            "第 %d 章校验降级(quarantined):%s,本章未经完整校验,"
            "跳过章后抽取/滚动摘要/契约提取(待人工复查后放行)",
            chapter_number, _why,
        )
        return f"{_why}:本章已隔离(quarantined),待人工复核"
    report("一致性门禁拦截:存在未消除的硬矛盾,本章已隔离(quarantined)")
    logger.warning(
        "第 %d 章被一致性门禁拦截(quarantined):%d 个 blocker 未消除,"
        "跳过章后抽取/滚动摘要/契约提取(待人工处理或放行)",
        chapter_number, len(blockers),
    )
    return "一致性门禁拦截:存在未消除的硬矛盾,本章已隔离(quarantined)"


# 兼容性再导出:degraded_of 在本模块只是转引,保持老引用可用
__all__ = ["FinalizeResult", "finalize_and_persist", "degraded_of"]
