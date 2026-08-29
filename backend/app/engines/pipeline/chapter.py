# app/engines/pipeline/chapter.py
# -*- coding: utf-8 -*-
"""逐章生成:上下文组装 → 草稿 → 定稿 → 滚动摘要。

上下文来源(见 docs/02-data-model.md 数据流):
  本章蓝图 + 下章蓝图 + 最近 2 章正文尾部(直接衔接)
  + 上一章章末交接契约(章末瞬态,衔接事实源)
  + 滚动前情摘要 + 倾向块
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterSummary, Outline, Project, WritingCard
from app.engines.common import chapter_architecture_brief, get_outline
from app.engines.consistency import (
    RESOURCE_FACT_TYPES,
    BibleService,
    ForeshadowScheduler,
    ledger_block,
)
from app.engines.consistency.checker import (
    blockers_of,
    blocker_fingerprint,
    check_chapter,
    continuity_score,
    persist_issues,
    triage_issues,
)
from app.engines.consistency.extractor import extract_and_apply, parse_llm_json
from app.engines.consistency.preflight import preflight_chapter
from app.engines.consistency.repetition import avoid_block, dedup_paragraphs
from app.engines.pipeline.handoff import extract_handoff_contract, load_handoff_block
from app.engines.timeline import persist_clock_issues
from app.engines.devices import devices_reminder_block, persist_device_issues
from app.engines.polish import ai_flavor_report
from app.engines.polish.polisher import _flavor_hits_block, deai_self_heal
from app.engines.editorial import (
    CONTINUITY_DIM,
    DIMS,
    apply_gate_fixes,
    apply_proofread_fixes,
    build_revision_directive,
    judge_passed,
    proofread_chapter,
    repair_chapter,
    review_chapter,
    store_proofread_snapshot,
    store_review_snapshot,
)
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import _PROFILE_KEY, render_style_block
from app.engines.tendency.cards import render_cards_block
from app.prompts.style_capsules import pairwise_examples_block, render_voice_block
from app.llm.base import LLMMessage, complete_text_with_budget
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import (
    CHAPTER_DRAFT_PROMPT,
    CHAPTER_FINALIZE_PROMPT,
    REVISE_CHAT_SYSTEM_PROMPT,
    REVISE_DISTILL_PROMPT,
    ROLLING_SUMMARY_PROMPT,
    STYLE_MEMO_UPDATE_PROMPT,
)
from app.engines.pipeline.word_guard import GuardResult, word_count_guard
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapter")

_RECENT_TAIL_CHARS = 900   # 每章取结尾多少字作直接上文
_RECENT_WINDOW = 2         # 直接注入最近几章的结尾
_REVISION_EXCERPT_CHARS = 1500  # 重写时上一版正文注入草稿 prompt 的截断长度


def _strip_meta(text: str) -> str:
    """清理模型输出的元信息:开头的 markdown 标题行 / 章节标题行。

    只删真正的「标题行」,不误伤正文首句。此前用
    `startswith("第") and "章" in [:12] and len<30` 会误删以「第」开头的正常
    短句(如「第二天,他没来。」)。改为精确匹配章节标题结构:
      - markdown 标题(# 开头)
      - 「第X章」「第X章 标题」「第X章:标题」这类整行标题,X 是数字或中文数字,
        且全行简短(<25 字)——正文叙述句几乎不会长这样。
    """
    # 「第」+数字/中文数字+「章」,后接空/标点/短标题,直到行尾
    chap_title = re.compile(
        r"^第[0-9零一二三四五六七八九十百千两]+章"
        r"([\s:：、·.。\-—《（(]*.{0,20})?$"
    )
    lines = text.strip().splitlines()
    while lines:
        head = lines[0].strip()
        if head.startswith("#") or (len(head) < 25 and chap_title.match(head)):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _beats_block(outline: Outline) -> str:
    """把本章场景节拍渲染成草稿的施工清单;无节拍时提示模型自行拆分。

    治"一句 100 字简述撑几千字"的结构松散:有节拍就按节拍逐个铺场景。
    """
    beats = [str(b).strip() for b in (outline.beats or []) if str(b).strip()]
    if not beats:
        return "(本章未预设节拍,请自行把剧情拆成若干有起伏的场景,不要平铺直叙)"
    lines = "\n".join(f"  {i}. {b}" for i, b in enumerate(beats, 1))
    return (
        "按以下场景节拍逐个推进(每个节拍写成一个有画面、有张力的场景,"
        "顺序可微调,但都要落实):\n" + lines
    )


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


def _revision_block(
    revision: str | None, previous_text: str, *, outline_changed: bool = False
) -> str:
    """重写意见注入块。

    - outline_changed=True(大纲改过后正文失配):不再注入旧正文节选——旧文基于
      旧大纲,注入会把模型锚回旧情节。改为明确指令"按新蓝图重新构思",用户补充
      意见(有则)一并带上。即使没有意见也生成该块:失配章的重写本质是重新生成。
    - 常规重写(大纲未变):上一版正文截断为前 _REVISION_EXCERPT_CHARS 字作反面
      参照,避免 token 爆炸;无意见则不生成。
    """
    revision = (revision or "").strip()
    if outline_changed:
        block = (
            "【按新大纲重写】本章大纲已更新,上一版正文基于旧大纲,与当前蓝图失配。\n"
            "请完全以上方最新蓝图为准重新构思本章情节,不要延续、不要修补旧版正文"
            "的情节安排;旧版正文中与旧大纲绑定的桥段应直接舍弃。\n"
        )
        if revision:
            block += f"用户补充意见(在满足新蓝图的前提下采纳):\n{revision}\n"
        return block
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


async def update_style_memo(
    db: Session, project: Project, chapter_number: int, chapter_text: str
) -> str | None:
    """写完一章后增量更新文风备忘(快模型档,失败不阻塞主流程)。

    与滚动摘要互补:摘要记"发生了什么",备忘记"这本书怎么写"(调性/人物声音/意象)。
    随书累积,注入后续章草稿,防长篇后段人物声音漂移、调性变淡。返回更新后的备忘。
    """
    prev = (project.style_memo or "").strip() or "(尚无,这是第一次积累)"
    try:
        memo = await get_adapter_for(Task.SUMMARY).ask(
            STYLE_MEMO_UPDATE_PROMPT.format(
                previous_memo=prev,
                chapter_number=chapter_number,
                chapter_text=chapter_text[:8000],
            )
        )
    except Exception:  # noqa: BLE001 — 备忘更新失败不影响正文与主流程
        logger.warning("文风备忘更新失败(第 %d 章),跳过", chapter_number, exc_info=True)
        return None
    memo = (memo or "").strip()
    if not memo:
        return None
    project.style_memo = memo
    db.flush()
    db.commit()
    return memo


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


async def apply_chapter_tail(
    db: Session,
    project: Project,
    chapter: Chapter,
    chapter_number: int,
    final: str,
    outline_title: str,
    report=None,
) -> dict:
    """门禁通过后的章后链路:抽取写圣经 → 滚动摘要 → 章末交接契约。

    generate_chapter 干净路径与 quarantined 放行端点(gate-release)共用。
    滚动摘要按「本章之前」的链尾现算(与生成时口径一致);契约提取放在摘要
    之后:摘要链是下游章生成的硬依赖,契约失败(只落 failed 行)绝不能拖累它。
    返回抽取统计。
    """

    def _report(stage: str) -> None:
        if report:
            try:
                report(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响主流程
                pass

    # ---- 章后抽取:状态变化写回圣经/伏笔表(闭环) ----
    # extract_and_apply 自管事务纪律(入口丢掉遗留读快照、LLM 前后各提交),
    # 故这里无需再手工 commit。
    _report("5/6 抽取状态写入故事圣经")
    logger.info("第 %d 章:抽取状态变化...", chapter_number)
    extraction_stats = await extract_and_apply(db, project.id, chapter_number, final)

    # ---- 滚动摘要更新 ----
    _report("6/6 更新前情摘要")
    logger.info("第 %d 章:更新前情摘要...", chapter_number)
    rolling = _rolling_summary(db, project.id, chapter_number)
    new_summary = await get_adapter_for(Task.SUMMARY).ask(
        ROLLING_SUMMARY_PROMPT.format(
            previous_summary=rolling,
            chapter_number=chapter_number,
            chapter_title=outline_title,
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

    # ---- 章末交接契约提取:章末瞬态(时间/地点/人物即时状态)落 chapter_states ----
    # 供下一章草稿注入(见 load_handoff_block)与下章门禁对照(见 checker)。
    # 失败只落 failed 行留痕,不阻塞主流程(docs/08 §4 可降级)。
    _report("提取章末交接契约")
    await extract_handoff_contract(
        db, chapter, chapter_number, final, get_adapter_for(Task.HANDOFF_EXTRACT)
    )

    # ---- 确定性故事时钟校验(advisory,不阻断):落 source=clock 建议 ----
    # 放在契约抽取【之后】——此刻本章 story_day 已入库,book_timeline 能看到本章这条,
    # 才能算牵涉本章的倒计时口径/天数倒流。纯算术、无 LLM;自吞异常 + rollback,
    # 绝不拖垮章后主链路(对齐 canon 建议的隔离范式)。门禁阻断路径另由 timeline_block
    # 把权威天数轴喂给 LLM 完成,这里是事后精确补网。
    try:
        persist_clock_issues(db, project.id, chapter, final)
    except Exception as exc:  # noqa: BLE001 — 时钟校验绝不阻塞主流程
        db.rollback()
        logger.warning("第 %d 章故事时钟校验失败(已跳过): %s", chapter_number, exc)

    # ---- 常驻装置断档校验(advisory,不阻断):落 source=devices 建议 ----
    # 同样放在契约抽取之后(此刻本章 devices_present 已入库)。与生成端催场块
    # (devices_reminder_block)配对成闭环:催过了本章仍没让装置出场,才在这里软报。
    # 与时钟校验各自 try/except,一边挂了不影响另一边。
    try:
        persist_device_issues(db, project.id, chapter, final)
    except Exception as exc:  # noqa: BLE001 — 装置校验绝不阻塞主流程
        db.rollback()
        logger.warning("第 %d 章常驻装置校验失败(已跳过): %s", chapter_number, exc)
    return extraction_stats


# prose 维未达标时的定向重写要求:「AI 腔/套话」靠同一模型自由发挥修不掉
# (实测每轮都因 prose=6 烧满回炉预算),必须把要求落到具体禁则上。
_PROSE_REWRITE_DIRECTIVE = (
    "文笔硬要求(上轮 prose 维未达标,重写必须逐条执行):"
    "①每段以具体画面、动作或对白开笔,禁止以心理独白或情绪陈述开段;"
    "②情绪一律外化成动作与感官细节,不写「他很紧张/她很难过」这类直陈;"
    "③比喻每段至多一处,禁用「仿佛/宛如/像是」连用,禁用「空气中弥漫着」"
    "「不知过了多久」「一瞬间,他明白了」这类万能套话;"
    "④对话删解释性台词,每句要么推进信息要么暴露性格;"
    "⑤长短句交错,连续三句同一结构必改写。"
)

# 维度中文名(死锁提示文案用)
_DIM_CN = {
    "plot": "情节", "prose": "文笔", "pacing": "节奏",
    "character": "人物", "continuity": "连续性",
}


def _with_prose_directive(directive: str, scores: dict, threshold: int) -> str:
    """prose 维低于阈值时,把「去 AI 腔」的具体禁则追加进重写指令。

    审校没报这一维(None/缺字段/脏值)视为不适用,原样返回——禁则只该在
    prose 确实挂了的时候出现。
    """
    raw = scores.get("prose")
    if raw is None:
        return directive
    try:
        prose = int(raw)
    except (TypeError, ValueError):
        return directive
    if prose >= threshold:
        return directive
    return f"{directive};{_PROSE_REWRITE_DIRECTIVE}" if directive else _PROSE_REWRITE_DIRECTIVE


def _gate_merged_review(review_result: dict, blockers: list[dict]) -> dict:
    """把门禁 blocker 问题并入主审结果,供 build_revision_directive 拼修订指令。"""
    merged = dict(review_result)
    merged["suggestions"] = list(review_result.get("suggestions") or []) + [
        {
            "evidence": i.get("evidence") or "",
            "issue": f"一致性矛盾({i.get('type') or 'state'}):{i.get('description')}",
            "fix": i.get("suggestion") or "",
        }
        for i in blockers
    ]
    return merged


async def generate_chapter(
    db: Session,
    project: Project,
    chapter_number: int,
    tendency: Tendency | None = None,
    progress=None,
    revision: str | None = None,
) -> tuple[Chapter, list[dict], dict, "GuardResult", dict, list[dict]]:
    """生成一章:写前审核 → 草稿 → 定稿 → 审校把关+一致性门禁 → 落库 → 抽取写圣经 → 摘要 → 契约。

    progress: 可选回调 fn(stage_text),六段各报一次(异步任务进度用)。
    revision: 重写时用户的修改意见;仅当本章已有正文时连同上一版
        (截断)注入草稿 prompt,首次生成传了也会被忽略。

    审校把关与一致性门禁(第 3/4 段,分级回炉):**门禁先行**——先确认事实对,
    再做文字精修,精修成果不会因方向错而作废。回炉循环(封顶 review_max_revisions
    轮,共享预算):
      ① 一致性门禁(checker:对照圣经 + 上一章契约 + 上章结尾原文)有 blocker →
         分诊(triage_issues):全部可定点修 → repair_chapter 一次小调用出精确
         替换对(apply_gate_fixes 逐字+唯一锚校验后应用),回门禁复查;修不掉/
         锚全失配/上轮刚修过 → 整章重写。auto_revise 关 → 不回炉直接隔离。
      ② 门禁干净才精修:校对硬伤自修(精确替换)+ 主审四维打分,continuity=9
         并入五维阈值硬判;不达标带主审意见(+prose 禁则)回炉重写。
    到点无论是否达标都接受当前最好的一版;回炉封顶仍有 blocker → 落库但
    status="quarantined":不做章后抽取(矛盾不进圣经)、不更新滚动摘要、
    不提契约;无 blocker 才走章后链路(apply_chapter_tail)。

    返回 (Chapter, 一致性门禁问题列表, 抽取统计, 字数守卫结果, 审校结果 dict, 写前审核警告列表)。
    审校结果含 scores(四维+continuity)/comment/suggestions/passed/
    revision_rounds/threshold/repair_rounds/repairs(定点修复明细);
    quarantined 时抽取统计为空 dict。
    写前审核警告(docs/08 §5.3)severity 一律 major,只警告不阻断,已随落库
    持久化(source="preflight");无契约/LLM 失败时为空列表。
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
    # 文风备忘(随书累积):拼进本次写作风格约束,后续章保持统一调性与人物声音。
    # 走 style_block 而非新占位符 —— draft/finalize 都吃 {style_directives},一处注入两处生效,
    # 且不必改模板占位符(避免模板与 format 两处只改一处导致 KeyError)。
    if (project.style_memo or "").strip():
        style_block += (
            "\n【本书文风备忘(随书累积,务必保持与前文一致的调性和人物声音)】\n"
            + project.style_memo.strip()
            + "\n"
        )
    # 写作手法卡:作者为本书启用的写法技巧,同样追加到 style_block(草稿/定稿/重写全生效)
    style_block += render_cards_block(
        db.query(WritingCard).filter(WritingCard.project_id == project.id).all()
    )
    # 文风范本(去 AI 味的「正向锚定」,治本项):作者在创作偏好档案里选的名家/预设
    # 胶囊 + 自备范文,渲染成「学这种笔法」的正样本追加进 style_block(草稿/定稿/去味
    # 重写全生效)。只靠负向禁令,模型会退回「全网文平均」腔调——恰恰最 AI;给正样本
    # 锚定「该像什么」才治本。存 global_tendency[_profile] 的 voice_key/voice_sample。
    _profile = (project.global_tendency or {}).get(_PROFILE_KEY) or {}
    if isinstance(_profile, dict):
        style_block += render_voice_block(
            str(_profile.get("voice_key") or ""),
            str(_profile.get("voice_sample") or ""),
        )

    rolling = _rolling_summary(db, project.id, chapter_number)
    recent = _recent_tail(db, project.id, chapter_number)
    # 上一章章末交接契约(docs/08 §5.2):与 recent_tail 并存——原文供语感,契约供事实。
    # 无契约的老章节/提取失败 → 空串,回退现状不报错。
    handoff_block = load_handoff_block(db, project.id, chapter_number)

    # ---- 写前审核(docs/08 §5.3):本章蓝图 vs 上一章契约,动笔前找矛盾 ----
    # 只警告不阻断(蓝图可以故意安排时间跳跃);无契约/无大纲跳过,LLM 失败降级。
    # 警告随落库持久化(source="preflight")并随返回值透出给生成响应。
    _report("写前审核(蓝图 vs 上章契约)")
    preflight_issues = await preflight_chapter(db, project.id, chapter_number, outline)

    # ---- 一致性引擎:硬约束 + 伏笔提醒 + 重复检测 ----
    bible = BibleService(db, project.id)
    hard_constraints = bible.hard_constraints_block(
        chapter_number,
        [str(c) for c in outline.characters_involved],
        exclude_types=RESOURCE_FACT_TYPES,
    )
    # 角色资源账本(P2):持有/能力两类事实从硬约束里分流出来单独渲染,自带闭集红线
    # (不许凭空掏出关键道具、新增要交代来源、用掉要写明)。空账本 → 空串,开篇几章零影响。
    resource_ledger = ledger_block(
        bible, chapter_number, [str(c) for c in outline.characters_involved]
    )
    # 已登场角色名册(闭集约束):防「凭空冒出常驻角色」(如大院一直写空荡荡,第8章却蹦出
    # 一个每天伺候起居的仆役)。与 hard_constraints 互补——后者只列本章涉及人物的状态,
    # 名册列全书已登场的人;草稿/定稿注入约束生成,同一份也喂给门禁(checker)比对。
    known_roster = bible.known_roster_block(chapter_number)
    scheduler = ForeshadowScheduler(db, project.id)
    foreshadow_reminders = scheduler.reminder_block(chapter_number)
    # 常驻装置催场(Phase 3):宪法里登记的金手指/信物断档到阈值就点名催场,治
    # 「女主有系统却多章消失」。无 canon 装置 / 老书契约无 devices_present → 空串零影响。
    device_reminders = devices_reminder_block(db, project.id, chapter_number)

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

    # 重写场景:失配章(大纲已更新)→ 按新蓝图重新构思,不注入旧正文;
    # 常规重写 → 用户修改意见连同上一版正文(截断)注入草稿 prompt
    existing = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    revision_block = _revision_block(
        revision,
        existing.final_content if existing else "",
        outline_changed=bool(existing and existing.is_stale),
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
            handoff_contract=handoff_block,
            hard_constraints=hard_constraints,
            known_roster=known_roster,
            resource_ledger=resource_ledger,
            foreshadow_reminders=foreshadow_reminders,
            device_reminders=device_reminders,
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
            chapter_beats=_beats_block(outline),
            next_chapter_brief=_next_chapter_brief(next_outline),
            word_number=project.target_words_per_chapter,
            scene_count=max(2, project.target_words_per_chapter // 1000),
            scene_words=project.target_words_per_chapter // max(2, project.target_words_per_chapter // 1000),
            style_directives=style_block,
        )
        d = _strip_meta(await get_adapter_for(Task.DRAFT).ask(draft_prompt))
        _report(finalize_label)
        # 定稿前的去味诊断(纯规则零成本):草稿先过 AI 味检测,命中句贴进定稿
        # prompt 定点改写 —— 复用润色端"先诊断后治疗"的成熟模式,生成端不再只靠自觉。
        flavor_hits = _flavor_hits_block(ai_flavor_report(d))
        finalize_prompt = CHAPTER_FINALIZE_PROMPT.format(
            chapter_number=chapter_number,
            chapter_title=outline.title,
            chapter_purpose=outline.chapter_purpose,
            foreshadowing=outline.foreshadowing,
            chapter_summary=outline.summary,
            rolling_summary=rolling,
            known_roster=known_roster,
            resource_ledger=resource_ledger,
            draft_text=d,
            flavor_hits=flavor_hits,
            # 定稿额外注入「AI 腔→人话」配对反例(给 pattern 比给 rule 有效);草稿不注入
            # 以控 token(草稿还没成文,无从对照,正向锚 voice 已在 style_block 里够用)
            style_directives=style_block + pairwise_examples_block(),
        )
        f = _strip_meta(await get_adapter_for(Task.FINALIZE).ask(finalize_prompt))
        return d, f

    logger.info("第 %d 章:生成草稿...", chapter_number)
    draft, final = await _compose(revision_block, "1/6 生成草稿", "2/6 定稿修订")

    # ---- 分级回炉:门禁先行(先修对,再修好),封顶 review_max_revisions 轮 ----
    # 循环两段:①一致性门禁有 blocker → 定点修复(patch)或整章重写,回门禁复查;
    # ②门禁干净才精修:校对自修 + 主审四维,不达标带意见(+prose 禁则)回炉重写。
    # 精修永远只发生在门禁干净的文本上——方向错了不浪费文字加工;主审触发的重写
    # 也回到①先过门禁,「精修完才发现方向错」从结构上不会发生。
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
    gate_issues: list[dict] = []  # 末轮一致性门禁结果(对应最终正文,落 chapter_issues 用)
    repair_rounds = 0  # 定点修复轮数(计入 revision_rounds,单独回显)
    last_repairs: dict = {}  # 末次定点修复明细 {applied, failed}(回显用)
    patch_tried = False  # 上一轮是否刚做过定点修复(修不掉的连续问题强制重写,防烧轮)
    rework_log: list[dict] = []  # 逐轮回炉原因(落快照:checker 意见稳不稳一眼可辨)
    prev_dim_scores: dict[str, int] = {}  # 上一轮主审各维得分(判断「无改善」)
    stalled_dims: set[str] = set()  # 连续 2 轮无改善的维度:不再为它重写
    prev_blocker_fps: set[str] = set()  # 上一轮 blocker 指纹(识别「同一问题复现」)
    while True:
        # ---- ① 一致性门禁(docs/08 §5.4):对照圣经 + 上章契约 + 上章结尾原文 ----
        # 有 blocker 不进精修:分诊后定点修复或重写,复查通过才往下走。门禁在落库前,
        # 拦住的矛盾不会抽进圣经。
        _report(
            "3/6 一致性门禁"
            if revision_rounds == 0
            else f"3/6 一致性门禁(第 {revision_rounds}/{max_revisions} 轮回炉)"
        )
        gate_issues = await check_chapter(
            db, project.id, chapter_number, final, rolling_summary=rolling
        )
        blockers = blockers_of(gate_issues)
        # continuity 随门禁结果先入 scores:精修段靠它判达标;预算烧在门禁段时
        # 主审没跑过,scores 至少带上 continuity 供 API/前端回显
        review_result.setdefault("scores", {})["continuity"] = continuity_score(gate_issues)
        if blockers:
            review_result["passed"] = False
            blocker_fps = {blocker_fingerprint(b) for b in blockers}
            all_recurring = bool(blocker_fps) and blocker_fps <= prev_blocker_fps
            prev_blocker_fps = blocker_fps
            if not auto_revise or revision_rounds >= max_revisions:
                break
            if all_recurring:
                # 同一批 blocker 上一轮就原样出现过:重写=重新抽签,消不掉还烧钱。
                # 止损隔离(矛盾照旧不进圣经),「疑似误报」的判断交给人工。
                # 本轮没有花任何重工作量,不计回炉轮数。
                review_result["gate_note"] = (
                    f"{len(blockers)} 个 blocker 连续 2 轮重写后仍未消除,"
                    "疑似检查误报;本章已隔离,请人工判断正文后放行或重写"
                )
                rework_log.append({
                    "round": revision_rounds, "trigger": "gate",
                    "blockers": [b.get("description", "")[:80] for b in blockers],
                    "note": "连续复现,止损隔离",
                })
                logger.info(
                    "第 %d 章 blocker 连续复现(%s…),止损隔离",
                    chapter_number, sorted(blocker_fps)[0][:40] if blocker_fps else "",
                )
                break
            revision_rounds += 1
            # 分诊:全部可定点修且上一轮没刚修过 → patch(一次小调用,保住好文);
            # 否则整章重写。修完不在这里复查——回到循环顶,门禁说了算。
            if not patch_tried and triage_issues(blockers) == "patch":
                patch_tried = True
                repair_rounds += 1
                _report(
                    f"3/6 一致性门禁(第 {revision_rounds}/{max_revisions} 轮·定点修复)"
                )
                fixes = await repair_chapter(chapter_number, final, blockers)
                new_final, applied, failed = apply_gate_fixes(final, fixes)
                if applied:
                    final = new_final
                    last_repairs = {"applied": applied, "failed": failed}
                    logger.info(
                        "第 %d 章门禁定点修复:%d 处(失配 %d 处),回门禁复查",
                        chapter_number, len(applied), len(failed),
                    )
                    continue
                logger.info(
                    "第 %d 章门禁问题不可定点修(%d 条修复全部未应用),转整章重写",
                    chapter_number, len(fixes),
                )
            patch_tried = False
            rework_log.append({
                "round": revision_rounds, "trigger": "gate",
                "blockers": [b.get("description", "")[:80] for b in blockers],
            })
            logger.info(
                "第 %d 章门禁拦截 %d 个 blocker,第 %d/%d 轮回炉(重写)",
                chapter_number, len(blockers), revision_rounds, max_revisions,
            )
            directive = build_revision_directive(_gate_merged_review(review_result, blockers))
            draft, final = await _compose(
                _revision_block(directive, final),
                f"3/6 一致性门禁(第 {revision_rounds}/{max_revisions} 轮回炉·重写草稿)",
                f"3/6 一致性门禁(第 {revision_rounds}/{max_revisions} 轮回炉·定稿)",
            )
            continue
        # ---- ② 门禁干净,精修:校对硬伤自修 + 主审四维达标判定 ----
        patch_tried = False
        _report(
            "4/6 审校把关"
            if revision_rounds == 0
            else f"4/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉)"
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
        # 主审打分(四维);continuity 已由门禁段写入(干净 → 9)
        review_result = await review_chapter(final, outline_block)
        review_result["scores"]["continuity"] = continuity_score(gate_issues)
        # 达标判定:五维阈值硬判(阈值调得再低,blocker 也已在①被拦)
        passed = judge_passed(review_result["scores"], threshold)
        review_result["passed"] = passed
        if passed:
            break
        if not auto_revise or revision_rounds >= max_revisions:
            break
        # ---- 回炉原因记账:同一维度连续 2 轮无改善 → 退出重写原因集 ----
        # 重写对同一个模型就是重新抽签:prose 6→6→6 的死锁靠它破——第 2 轮
        # 还停在原地,就不再为这个维度烧草稿+定稿(实测 4 章 12 轮 prose 纹丝不动)。
        scores_now = review_result["scores"]
        failing = [
            d for d in (*DIMS, CONTINUITY_DIM)
            if int(scores_now.get(d) or 0) < threshold
        ]
        stalled_dims &= set(failing)  # 已达标的维度不再算停滞
        retryable: list[str] = []
        for d in failing:
            now_v, prev_v = int(scores_now.get(d) or 0), prev_dim_scores.get(d)
            if prev_v is not None and now_v <= prev_v:
                stalled_dims.add(d)
            elif prev_v is not None and now_v > prev_v:
                stalled_dims.discard(d)  # 有改善,再给一轮机会
            if d not in stalled_dims:
                retryable.append(d)
        rework_log.append({
            "round": revision_rounds + 1, "trigger": "review",
            "failing": list(failing), "stalled": sorted(stalled_dims),
        })
        if not retryable:
            # 所有未达标维度都连续两轮无改善:再重写注定同样结果,接受当前版本
            review_result["stall_note"] = (
                "未达标维度连续 2 轮回炉无改善,已停止重写并接受当前版本;"
                "建议写手与审校使用不同模型,或适当调低达标线"
            )
            logger.info(
                "第 %d 章 %s 维连续无改善,停止重写,接受当前版本",
                chapter_number, "/".join(failing),
            )
            break
        revision_rounds += 1
        logger.info(
            "第 %d 章未通过(五维=%s,阈值=%d,待改维度=%s),第 %d/%d 轮回炉",
            chapter_number, review_result["scores"], threshold,
            "/".join(retryable), revision_rounds, max_revisions,
        )
        directive = build_revision_directive(review_result)
        if "prose" in retryable:
            directive = _with_prose_directive(
                directive, review_result.get("scores") or {}, threshold
            )
        draft, final = await _compose(
            _revision_block(directive, final),
            f"4/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉·草稿)",
            f"4/6 审校把关(第 {revision_rounds}/{max_revisions} 轮回炉·定稿)",
        )
        prev_dim_scores = {
            d: int(scores_now.get(d) or 0) for d in (*DIMS, CONTINUITY_DIM)
        }
    review_result["revision_rounds"] = revision_rounds
    review_result["repair_rounds"] = repair_rounds
    review_result["repairs"] = last_repairs
    review_result["rework_log"] = rework_log
    review_result["threshold"] = threshold
    review_result["proofread_fixed"] = proofread_fixed
    # 死锁提示:停滞维度显式告知(模型配比可能系统性不可达),决策留给作者
    if stalled_dims:
        review_result["hints"] = [
            f"「{_DIM_CN.get(d, d)}」维连续多轮回炉无改善:当前写手/审校模型配比下"
            f"该维度可能无法稳定达到阈值 {threshold}。建议写手与审校使用不同模型,"
            "或在项目设置中适当调低达标线。"
            for d in sorted(stalled_dims)
        ]
    reviewed_text = final  # 审校/门禁对应的正文(字数守卫可能在其后改动,指纹以此为准)
    logger.info(
        "第 %d 章审校+门禁完成:通过=%s,五维=%s,blocker=%d,回炉 %d 轮(定点修 %d)",
        chapter_number, review_result.get("passed"),
        review_result.get("scores"), len(blockers_of(gate_issues)),
        revision_rounds, repair_rounds,
    )

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
    final, _deai_before, _deai_after = await deai_self_heal(
        final, style_block, progress=_report
    )
    if _deai_after.score < _deai_before.score:
        logger.info(
            "第 %d 章 AI 味自愈:%.1f → %.1f",
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
    chapter.status = "quarantined" if blockers else "pending_review"
    # 审校快照落库:编辑部打开时回显本次主审结果,免去用户再点一次「请主编审读」
    store_review_snapshot(chapter, review_result, "generation", reviewed_text)
    # 校对快照落库:回显生成时自动修复了哪些硬伤(指纹与主审一致,正文改动同步失效)
    store_proofread_snapshot(chapter, last_fixed_issues, "generation", reviewed_text)
    db.flush()
    # 正文立刻提交:后面章后链路还有数分钟 LLM 调用,
    # 不能拿着写锁跨这些 await(会把并发写卡到超时),失败也不该丢正文。
    db.commit()
    # issues 落库:purge 本章旧 open 按当前结果重建(幂等);
    # 指纹已变的旧 ignored 清除(不再生效),未变的保留(用户已确认忽略)。
    persist_issues(db, chapter, gate_issues, source="gate", text=final)
    # 写前审核警告同法落库(source="preflight"),与门禁问题同面板展示
    persist_issues(db, chapter, preflight_issues, source="preflight", text=final)
    db.commit()

    if blockers:
        _report("一致性门禁拦截:存在未消除的硬矛盾,本章已隔离(quarantined)")
        logger.warning(
            "第 %d 章被一致性门禁拦截(quarantined):%d 个 blocker 未消除,"
            "跳过章后抽取/滚动摘要/契约提取(待人工处理或放行)",
            chapter_number, len(blockers),
        )
        return chapter, gate_issues, {}, guard_result, review_result, preflight_issues

    # ---- 章后链路(门禁通过才走):抽取写圣经 → 滚动摘要 → 章末契约 ----
    extraction_stats = await apply_chapter_tail(
        db, project, chapter, chapter_number, final, outline.title, report=_report
    )

    # ---- 重写场景:下游章节的滚动摘要基于旧文,重建 ----
    # 文风备忘:随书累积"这本书怎么写"(与摘要互补),注入后续章草稿
    _report("文风备忘更新")
    await update_style_memo(db, project, chapter_number, final)

    rebuilt = await rebuild_summaries_after(db, project, chapter_number, progress)
    if rebuilt:
        logger.info("第 %d 章重写,已重建下游摘要: %s", chapter_number, rebuilt)

    logger.info("第 %d 章完成,共 %d 字。", chapter_number, chapter.word_count)
    return chapter, gate_issues, extraction_stats, guard_result, review_result, preflight_issues


# =============== 重写研讨(对话式:聊清不满意 → 蒸馏成重写要求)===============
# 与架构研讨(discuss_architecture)同构的「续聊 + 独立蒸馏」两段式,只是上下文
# 从整本书架构换成单章蓝图+正文。蒸馏出的 directive 回填进重写文本框,作为
# generate_chapter 的 revision 参数走既有 _revision_block 注入草稿,管线零改动。
_MAX_REVISE_CHAT_TURNS = 40
_MAX_REVISE_MSG_LEN = 2000
_MAX_REVISE_CHAPTER_CHARS = 3000  # 当前正文注入 system 时截断,防 token 膨胀


async def _revise_complete(adapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:空正文放大预算重试 + 用量记账。

    别在这里自己写"空串就翻倍"的循环:complete() 遇空正文是抛 EmptyContentError,
    统一交给 complete_text_with_budget 处理(见 llm/base.py)。
    """
    return await complete_text_with_budget(adapter, messages)


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

    # ② 蒸馏:把含最新回复的完整对话提炼成「修改意见 + 档位建议」(独立调用,不污染对话)
    directive, level = await _distill_revision(adapter, turns, reply)
    return {"reply": reply, "directive": directive, "suggested_level": level}


async def _distill_revision(adapter, turns: list[dict], reply: str) -> tuple[str, str | None]:
    """把含最新回复的完整对话蒸馏成「修改意见 directive + 档位建议 level」。

    独立 ask 调用(不污染对话);蒸馏出"尚无明确意见"时约定回空/短横线,归一化成空串。
    失败不抛(蒸馏不该阻塞对话本身),返回 ("", None) 让前端中性呈现两个档位选项。
    同步 discuss_revision 与流式 discuss_revision_stream 共用这一份,行为一致。
    """
    transcript = _format_revise_transcript(turns, reply)
    try:
        raw = (await adapter.ask(REVISE_DISTILL_PROMPT.format(transcript=transcript))).strip()
        if raw and raw != "-":
            parsed = parse_llm_json(raw)
            if isinstance(parsed.get("directive"), str):
                # JSON 契约:directive 正文 + level 档位建议(polish=锁情节优化 / regenerate=重生成)
                lv = parsed.get("level")
                return parsed["directive"].strip(), (lv if lv in ("polish", "regenerate") else None)
            # 模型没按 JSON 输出:整段当意见,不给档位建议
            return raw, None
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("重写研讨蒸馏失败,directive 置空", exc_info=True)
    return "", None


async def discuss_revision_stream(
    messages: list[dict],
    *,
    blueprint_block: str,
    chapter_block: str,
):
    """流式版 discuss_revision(SSE 打字机):逐字产出 reply,收尾给 directive + 档位建议。

    产出 (kind, payload):
      ("token", str)  reply 的增量文字
      ("done", {"reply": str, "directive": str, "suggested_level": str | None})
    校验/system 构造同 discuss_revision(同步孪生);reply 流式吐完后再做一次(非流式)蒸馏。
    流式路径不重试空回复、不记账(与 openai_compatible._complete_via_stream 的取舍一致)。
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
    system = REVISE_CHAT_SYSTEM_PROMPT.format(
        blueprint_block=blueprint_block,
        chapter_block=chapter_block[:_MAX_REVISE_CHAPTER_CHARS] or "(本章还没有正文)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_REVISE_MSG_LEN])
        for m in turns
    ]
    chunks: list[str] = []
    async for delta in adapter.stream(chat_messages):
        if not delta:
            continue
        chunks.append(delta)
        yield ("token", delta)
    reply = "".join(chunks).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")
    directive, level = await _distill_revision(adapter, turns, reply)
    yield ("done", {"reply": reply, "directive": directive, "suggested_level": level})
