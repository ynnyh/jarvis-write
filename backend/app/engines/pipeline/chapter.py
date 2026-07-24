# app/engines/pipeline/chapter.py
# -*- coding: utf-8 -*-
"""逐章生成:上下文组装 → 草稿 → 定稿 → 滚动摘要 → 入向量库。

上下文来源(见 docs/02-data-model.md 数据流):
  本章蓝图 + 下章蓝图 + 最近 2 章正文尾部(直接衔接)
  + 滚动前情摘要 + 语义检索历史片段(排除最近 2 章)+ 倾向块
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterSummary, Outline, Project
from app.engines.common import chapter_architecture_brief, get_outline
from app.engines.consistency import BibleService, ForeshadowScheduler
from app.engines.consistency.checker import check_chapter
from app.engines.consistency.extractor import extract_and_apply
from app.engines.consistency.repetition import avoid_block
from app.engines.editorial import (
    apply_proofread_fixes,
    build_revision_directive,
    judge_passed,
    proofread_chapter,
    review_chapter,
    store_proofread_snapshot,
    store_review_snapshot,
)
from app.engines.memory import ChapterMemory
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.base import LLMMessage
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import (
    CHAPTER_DRAFT_PROMPT,
    CHAPTER_FINALIZE_PROMPT,
    REVISE_CHAT_SYSTEM_PROMPT,
    REVISE_DISTILL_PROMPT,
    ROLLING_SUMMARY_PROMPT,
)
from app.engines.pipeline.word_guard import GuardResult, word_count_guard
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapter")

_RECENT_TAIL_CHARS = 900   # 每章取结尾多少字作直接上文
_RECENT_WINDOW = 2         # 直接注入最近几章的结尾
_REVISION_EXCERPT_CHARS = 1500  # 重写时上一版正文注入草稿 prompt 的截断长度


def _strip_meta(text: str) -> str:
    """清理模型输出的元信息:开头的 markdown 标题/章节名行。"""
    lines = text.strip().splitlines()
    while lines and (
        lines[0].strip().startswith("#")
        or lines[0].strip().startswith("第")
        and "章" in lines[0][:12]
        and len(lines[0].strip()) < 30
    ):
        lines.pop(0)
    return "\n".join(lines).strip()


def _next_chapter_brief(nxt: Outline | None) -> str:
    if nxt is None:
        return "(本章为最后一章,收束全书)"
    return (
        f"第{nxt.chapter_number}章《{nxt.title}》:{nxt.summary}"
        f"(伏笔操作:{nxt.foreshadowing})"
    )


def _recent_tail(db: Session, project_id: int, current: int) -> str:
    """取最近 _RECENT_WINDOW 章定稿的结尾拼接。"""
    parts: list[str] = []
    for n in range(max(1, current - _RECENT_WINDOW), current):
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch and ch.final_content:
            parts.append(f"(第{n}章结尾)…{ch.final_content[-_RECENT_TAIL_CHARS:]}")
    return "\n\n".join(parts) or "(本章是第一章,无上文)"


def _revision_block(revision: str | None, previous_text: str) -> str:
    """重写意见注入块:仅当上一版正文存在且用户给了修改意见时生成。

    上一版正文截断为前 _REVISION_EXCERPT_CHARS 字,作反面参照,避免 token 爆炸。
    """
    revision = (revision or "").strip()
    if not revision or not previous_text.strip():
        return ""
    excerpt = previous_text[:_REVISION_EXCERPT_CHARS]
    if len(previous_text) > _REVISION_EXCERPT_CHARS:
        excerpt += "……(后略)"
    return (
        "【重写要求】这是重写:上一版正文用户不满意,修改意见如下:\n"
        f"{revision}\n"
        "请在保持本章蓝图、人物状态与伏笔约束不变的前提下,针对以上意见改进。\n\n"
        "【上一版正文(反面参照,仅供对照问题,不可照抄)】\n"
        f"{excerpt}"
    )


def _rolling_summary(db: Session, project_id: int, current: int) -> str:
    row = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project_id,
            ChapterSummary.chapter_number < current,
        )
        .order_by(ChapterSummary.chapter_number.desc())
        .first()
    )
    return row.rolling_summary if row else "(无,本章为开篇)"


async def rebuild_summaries_after(
    db: Session, project: Project, changed_chapter: int, progress=None
) -> list[int]:
    """重建第 changed_chapter 章之后的滚动摘要链。

    重写/手改某章正文后,后续章的滚动摘要都基于旧文,必须顺序重算
    (快模型档,每章一次调用)。返回重建的章号列表。
    """
    laters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number > changed_chapter,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    # 只有当后续章已存在摘要时才需要重建
    later_nums = [c.chapter_number for c in laters]
    if not later_nums:
        return []

    rebuilt: list[int] = []
    for ch in laters:
        if progress:
            try:
                progress(f"重建第 {ch.chapter_number} 章前情摘要")
            except Exception:  # noqa: BLE001
                pass
        prev = _rolling_summary(db, project.id, ch.chapter_number)
        outline = get_outline(db, project.id, ch.chapter_number)
        title = outline.title if outline else ""
        text = ch.final_content
        # 读完即提交,释放读快照:LLM 调用期间用量记账会在别的连接提交,让旧快照过期,
        # 之后 UPDATE 升级写锁会撞 SQLITE_BUSY(WAL 下该错误不走 busy_timeout,直接失败)。
        db.commit()
        new_summary = await get_adapter_for(Task.SUMMARY).ask(
            ROLLING_SUMMARY_PROMPT.format(
                previous_summary=prev,
                chapter_number=ch.chapter_number,
                chapter_title=title,
                chapter_text=text,
            )
        )
        row = (
            db.query(ChapterSummary)
            .filter(
                ChapterSummary.project_id == project.id,
                ChapterSummary.chapter_number == ch.chapter_number,
            )
            .first()
        )
        if row is None:
            row = ChapterSummary(
                project_id=project.id, chapter_number=ch.chapter_number
            )
            db.add(row)
        row.rolling_summary = new_summary.strip()
        db.flush()
        # 每章提交一次:别拿着写事务跨下一轮 LLM 调用(阻塞并发写,快照也会过期)
        db.commit()
        rebuilt.append(ch.chapter_number)

    logger.info("摘要链重建完成: %s", rebuilt)
    return rebuilt


async def generate_chapter(
    db: Session,
    project: Project,
    chapter_number: int,
    tendency: Tendency | None = None,
    progress=None,
    revision: str | None = None,
) -> tuple[Chapter, list[dict], dict, "GuardResult", dict]:
    """生成一章:草稿 → 定稿 → 审校把关 → 一致性检查 → 抽取写圣经 → 摘要 → 入库。

    progress: 可选回调 fn(stage_text),六段各报一次(异步任务进度用)。
    revision: 重写时用户的修改意见;仅当本章已有正文时连同上一版
        (截断)注入草稿 prompt,首次生成传了也会被忽略。

    审校把关(第 3 段):定稿后自动校对(硬伤精确替换自修)+ 主审打分,按项目
    review_pass_threshold 硬判达标;不达标且 review_auto_revise 开启时,带主审意见
    回炉重走草稿+定稿,封顶 review_max_revisions 轮,到点接受当前最好的一版。

    返回 (Chapter, 一致性问题列表, 抽取统计, 字数守卫结果, 审校结果 dict)。
    审校结果含 scores/comment/suggestions/passed/revision_rounds/threshold。
    """

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    outline = get_outline(db, project.id, chapter_number)
    if outline is None:
        raise ValueError(f"第 {chapter_number} 章没有大纲,请先生成蓝图")
    next_outline = get_outline(db, project.id, chapter_number + 1)

    assembled = assemble_tendency("chapter", tendency, project.global_tendency)
    style_block = render_style_block(assembled)

    rolling = _rolling_summary(db, project.id, chapter_number)
    recent = _recent_tail(db, project.id, chapter_number)

    # 语义检索:排除直接上文窗口内的章
    memory = ChapterMemory(project.id)
    query = f"{outline.title} {outline.summary} {' '.join(map(str, outline.characters_involved))}"
    retrieved = await memory.retrieve(
        query, exclude_after=chapter_number - _RECENT_WINDOW
    )
    retrieved_text = "\n---\n".join(retrieved) or "(暂无)"

    # ---- 一致性引擎:硬约束 + 伏笔提醒 + 重复检测 ----
    bible = BibleService(db, project.id)
    hard_constraints = bible.hard_constraints_block(
        chapter_number, [str(c) for c in outline.characters_involved]
    )
    scheduler = ForeshadowScheduler(db, project.id)
    foreshadow_reminders = scheduler.reminder_block(chapter_number)

    recent_full = [
        c.final_content
        for c in db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number < chapter_number,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number.desc())
        .limit(3)
    ]
    avoid_repetition = avoid_block(recent_full)

    # 重写场景:用户给了修改意见 → 连同上一版正文(截断)注入草稿 prompt
    existing = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    revision_block = _revision_block(
        revision, existing.final_content if existing else ""
    )

    # ---- 草稿 + 定稿(封装成 _compose,审校回炉时复用) ----
    async def _compose(rev_block: str, draft_label: str, finalize_label: str) -> tuple[str, str]:
        """草稿 → 定稿。rev_block 注入草稿 prompt;返回 (草稿, 定稿)。"""
        _report(draft_label)
        draft_prompt = CHAPTER_DRAFT_PROMPT.format(
            chapter_number=chapter_number,
            chapter_title=outline.title,
            architecture_brief=chapter_architecture_brief(project),
            rolling_summary=rolling,
            recent_tail=recent,
            retrieved_context=retrieved_text,
            hard_constraints=hard_constraints,
            foreshadow_reminders=foreshadow_reminders,
            avoid_repetition=avoid_repetition,
            revision_block=rev_block,
            chapter_role=outline.chapter_role,
            chapter_purpose=outline.chapter_purpose,
            suspense_level=outline.suspense_level,
            foreshadowing=outline.foreshadowing,
            characters_involved="、".join(map(str, outline.characters_involved)) or "(未指定)",
            key_items="、".join(map(str, outline.key_items)) or "无",
            scene_location=outline.scene_location,
            chapter_summary=outline.summary,
            next_chapter_brief=_next_chapter_brief(next_outline),
            word_number=project.target_words_per_chapter,
            scene_count=max(2, project.target_words_per_chapter // 1000),
            scene_words=project.target_words_per_chapter // max(2, project.target_words_per_chapter // 1000),
            style_directives=style_block,
        )
        d = _strip_meta(await get_adapter_for(Task.DRAFT).ask(draft_prompt))
        _report(finalize_label)
        finalize_prompt = CHAPTER_FINALIZE_PROMPT.format(
            chapter_number=chapter_number,
            chapter_title=outline.title,
            chapter_purpose=outline.chapter_purpose,
            foreshadowing=outline.foreshadowing,
            chapter_summary=outline.summary,
            rolling_summary=rolling,
            draft_text=d,
            style_directives=style_block,
        )
        f = _strip_meta(await get_adapter_for(Task.FINALIZE).ask(finalize_prompt))
        return d, f

    logger.info("第 %d 章:生成草稿...", chapter_number)
    draft, final = await _compose(revision_block, "1/6 生成草稿", "2/6 定稿修订")

    # ---- 审校把关:校对硬伤自修 + 主审达标判定 + 有上限自动回炉 ----
    # 达标与否由后端按项目阈值硬判;不达标则带主审意见回炉重走草稿+定稿,封顶
    # review_max_revisions 轮,到点无论是否达标都接受当前最好的一版(不会无限回炉)。
    threshold = project.review_pass_threshold
    auto_revise = project.review_auto_revise
    max_revisions = project.review_max_revisions
    outline_block = (
        f"标题:{outline.title}\n目的:{outline.chapter_purpose}\n概要:{outline.summary}"
    )
    review_result: dict = {}
    revision_rounds = 0
    proofread_fixed = 0  # 校对累计自动修复的硬伤数(回显给用户看"校对跑过了")
    last_fixed_issues: list[dict] = []  # 末轮校对自动修复的清单(对应最终正文,回显用)
    while True:
        _report(
            "3/6 审校把关"
            if revision_rounds == 0
            else f"3/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉)"
        )
        # 校对硬伤:错字/语病/标点/重复,精确替换自修(幻觉片段已在引擎里过滤)
        proof = await proofread_chapter(final)
        round_fixed: list[dict] = []
        if proof["issues"]:
            final, _applied, _failed = apply_proofread_fixes(final, proof["issues"])
            proofread_fixed += len(_applied)
            # 留下真正修掉的那几条(带类型/理由),供编辑部「校对」tab 回显
            applied_originals = {a["original"] for a in _applied}
            round_fixed = [it for it in proof["issues"] if it["original"] in applied_originals]
        last_fixed_issues = round_fixed
        # 主审打分 + 按项目阈值硬判达标
        review_result = await review_chapter(final, outline_block)
        if judge_passed(review_result["scores"], threshold):
            review_result["passed"] = True
            break
        review_result["passed"] = False
        if not auto_revise or revision_rounds >= max_revisions:
            break
        # 不达标 → 主审意见拼成重写指令,回炉重走草稿+定稿
        revision_rounds += 1
        logger.info(
            "第 %d 章审校未达标(四维=%s,阈值=%d),第 %d/%d 轮回炉",
            chapter_number, review_result["scores"], threshold,
            revision_rounds, max_revisions,
        )
        directive = build_revision_directive(review_result)
        draft, final = await _compose(
            _revision_block(directive, final),
            f"3/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉·草稿)",
            f"3/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉·定稿)",
        )
    review_result["revision_rounds"] = revision_rounds
    review_result["threshold"] = threshold
    review_result["proofread_fixed"] = proofread_fixed
    reviewed_text = final  # 审校对应的正文(字数守卫可能在其后改动,指纹以此为准)
    logger.info(
        "第 %d 章审校把关完成:达标=%s,四维=%s,回炉 %d 轮",
        chapter_number, review_result.get("passed"),
        review_result.get("scores"), revision_rounds,
    )

    # ---- 字数守卫:超标压缩/拆章(只对审校后的最终定稿跑一次) ----
    guard_result = await word_count_guard(
        db, project, chapter_number, outline, final, style_block, report=_report
    )
    final = guard_result.final_text

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
    chapter.status = "finalized"
    # 审校快照落库:编辑部打开时回显本次主审结果,免去用户再点一次「请主编审读」
    store_review_snapshot(chapter, review_result, "generation", reviewed_text)
    # 校对快照落库:回显生成时自动修复了哪些硬伤(指纹与主审一致,正文改动同步失效)
    store_proofread_snapshot(chapter, last_fixed_issues, "generation", reviewed_text)
    db.flush()
    # 正文立刻提交:后面一致性/抽取/摘要还有数分钟 LLM 调用,
    # 不能拿着写锁跨这些 await(会把并发写卡到超时),失败也不该丢正文。
    db.commit()

    # ---- 一致性检查(vs 本章之前的圣经状态) ----
    _report("4/6 一致性检查")
    logger.info("第 %d 章:一致性检查...", chapter_number)
    issues = await check_chapter(
        db, project.id, chapter_number, final, rolling_summary=rolling
    )

    # ---- 章后抽取:状态变化写回圣经/伏笔表(闭环) ----
    _report("5/6 抽取状态写入故事圣经")
    logger.info("第 %d 章:抽取状态变化...", chapter_number)
    # extract_and_apply 自管事务纪律(入口丢掉上面 check_chapter 遗留的读快照、
    # LLM 前后各提交),故这里无需再手工 commit —— S1「越写到后面越容易在 4/5 抽取处
    # 随机报 database is locked」的根因正在于此前少了这道快照释放。
    extraction_stats = await extract_and_apply(
        db, project.id, chapter_number, final
    )

    # ---- 滚动摘要更新 ----
    _report("6/6 更新前情摘要")
    logger.info("第 %d 章:更新前情摘要...", chapter_number)
    new_summary = await get_adapter_for(Task.SUMMARY).ask(
        ROLLING_SUMMARY_PROMPT.format(
            previous_summary=rolling,
            chapter_number=chapter_number,
            chapter_title=outline.title,
            chapter_text=final,
        )
    )
    srow = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project.id,
            ChapterSummary.chapter_number == chapter_number,
        )
        .first()
    )
    if srow is None:
        srow = ChapterSummary(project_id=project.id, chapter_number=chapter_number)
        db.add(srow)
    srow.rolling_summary = new_summary.strip()
    db.flush()
    db.commit()

    # ---- 入向量库(失败自动降级,不阻塞) ----
    await memory.add_chapter(chapter_number, final)

    # ---- 重写场景:下游章节的滚动摘要基于旧文,重建 ----
    rebuilt = await rebuild_summaries_after(db, project, chapter_number, progress)
    if rebuilt:
        logger.info("第 %d 章重写,已重建下游摘要: %s", chapter_number, rebuilt)

    logger.info("第 %d 章完成,共 %d 字。", chapter_number, chapter.word_count)
    return chapter, issues, extraction_stats, guard_result, review_result


# =============== 重写研讨(对话式:聊清不满意 → 蒸馏成重写要求)===============
# 与架构研讨(discuss_architecture)同构的「续聊 + 独立蒸馏」两段式,只是上下文
# 从整本书架构换成单章蓝图+正文。蒸馏出的 directive 回填进重写文本框,作为
# generate_chapter 的 revision 参数走既有 _revision_block 注入草稿,管线零改动。
_MAX_REVISE_CHAT_TURNS = 40
_MAX_REVISE_MSG_LEN = 2000
_MAX_REVISE_CHAPTER_CHARS = 3000  # 当前正文注入 system 时截断,防 token 膨胀


async def _revise_complete(adapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:空回复重试 + 用量记账(对齐 ask 的兜底)。"""
    original_max = adapter.max_tokens
    try:
        for _ in range(3):
            resp = await adapter.complete(messages)
            adapter._record_usage(resp)
            content = (resp.content or "").strip()
            if content:
                return content
            adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
        raise RuntimeError("模型连续 3 次返回空回复")
    finally:
        adapter.max_tokens = original_max


def _format_revise_transcript(turns: list[dict], latest_reply: str) -> str:
    lines = [
        f"{'作者' if m['role'] == 'user' else '编辑'}:{(m['content'] or '').strip()}"
        for m in turns
    ]
    lines.append(f"编辑:{latest_reply}")
    return "\n".join(lines)


async def discuss_revision(
    messages: list[dict],
    *,
    blueprint_block: str,
    chapter_block: str,
) -> dict:
    """就某一章的重写与作者多轮研讨:聊清"到底哪里不满意",蒸馏出重写要求。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - blueprint_block/chapter_block:本章蓝图与当前正文节选,供编辑理解上下文。

    返回 {reply, directive};directive 为蒸馏出的修改意见(可为空串),前端回填进
    重写文本框,确认后作为 revision 参数去重写本章。
    """
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_REVISE_CHAT_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    adapter = get_adapter_for(Task.DRAFT)

    # ① 续聊:system(带蓝图+正文上下文)+ 对话历史
    system = REVISE_CHAT_SYSTEM_PROMPT.format(
        blueprint_block=blueprint_block,
        chapter_block=chapter_block[:_MAX_REVISE_CHAPTER_CHARS] or "(本章还没有正文)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_REVISE_MSG_LEN])
        for m in turns
    ]
    reply = (await _revise_complete(adapter, chat_messages)).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")

    # ② 蒸馏:把含最新回复的完整对话提炼成「修改意见」(独立调用,不污染对话)
    transcript = _format_revise_transcript(turns, reply)
    directive = ""
    try:
        raw = (await adapter.ask(REVISE_DISTILL_PROMPT.format(transcript=transcript))).strip()
        # 蒸馏出"尚无明确意见"时约定回一个短横线,归一化成空串
        if raw and raw != "-":
            directive = raw
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("重写研讨蒸馏失败,directive 置空", exc_info=True)

    return {"reply": reply, "directive": directive}
