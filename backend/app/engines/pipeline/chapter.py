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
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Chapter,
    Outline,
    Project,
    WritingCard,
)
from app.engines.common import chapter_architecture_brief, get_outline
from app.engines.consistency import (
    RESOURCE_FACT_TYPES,
    BibleService,
    ForeshadowScheduler,
    ledger_block,
)
from app.engines.consistency.foreshadow_agenda import build_agenda, render_agenda_block
from app.engines.consistency.reader_knowledge import build_reader_view, render_twist_block
from app.engines.consistency.checker import persist_issues
from app.engines.consistency.preflight import preflight_chapter
from app.engines.consistency.repetition import avoid_block
from app.engines.consistency.motifs import banned_block, ledger_avoid_block
from app.engines.pipeline.handoff import load_handoff_block
from app.engines.devices import devices_reminder_block
from app.engines.polish import ai_flavor_report
from app.engines.polish.polisher import (
    _flavor_hits_block,
    fatigue_block,
    memo_notes_block,
)
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import _PROFILE_KEY, dna_block_of, render_style_block
from app.engines.tendency.cards import render_cards_block
from app.prompts.style_capsules import pairwise_examples_block, render_voice_block
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import CHAPTER_FINALIZE_PROMPT
from app.engines.pipeline.word_guard import GuardResult
from app.engines.pipeline.chapter_compose import (
    ChapterContext,
    Composer,
    _beats_block,
    _deai_rules_block,
    _drama_task_block,
    _next_chapter_brief,
    _strip_meta,
)
from app.engines.pipeline.chapter_finalize import finalize_and_persist
from app.engines.pipeline.chapter_rework import (
    _gate_merged_review,
    _revision_block,
    _with_prose_directive,
    review_and_rework,
)
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapter")

# 兼容性再导出:以下符号已随拆解搬到子模块,老调用方仍从这里导入,不动。
# (test_chapter_drama_task 导入 _deai_rules_block/_drama_task_block;
#  test_consistency_guardrail 导入 _with_prose_directive;
#  api/chapters/* 与 rewrite_session 导入 _revision_block/_strip_meta 等)
from app.engines.pipeline.chapter_compose import (  # noqa: E402,F401
    _DEAI_ESCALATE_HITS,
    _DEFAULT_ROLE_TASK,
    _ROLE_TASKS,
    _SUSPENSE_TASKS,
)
from app.engines.pipeline.chapter_rework import (  # noqa: E402,F401
    _DIM_CN,
    _PROSE_REWRITE_DIRECTIVE,
    _REVISION_EXCERPT_CHARS,
)

# 兼容性再导出:章后维护/共享上下文/重写研讨已拆到子模块,老调用方
# (api/chapters/*、diagnosis、outline_discuss 等)仍从这里导入,不动。
from app.engines.pipeline.chapter_context import (  # noqa: E402,F401
    _RECENT_TAIL_CHARS,
    _RECENT_WINDOW,
    _recent_tail,
    _rolling_summary,
)
from app.engines.pipeline.chapter_maintenance import (  # noqa: E402,F401
    apply_chapter_tail,
    rebuild_summaries_after,
    update_style_memo,
)
from app.engines.pipeline.rewrite_session import (  # noqa: E402,F401
    _MAX_REVISE_CHAT_TURNS,
    _MAX_REVISE_MSG_LEN,
    _distill_revision,
    _format_revise_transcript,
    _revise_complete,
    discuss_revision,
    discuss_revision_stream,
)

# 兼容再导出:常量随 helper 一起搬到了子模块(chapter_compose / chapter_rework),
# 老调用方与测试仍从 chapter.py 读,故在此显式再导出(见本文件顶部 import 块)。
# 注意:_DEAI_ESCALATE_HITS 的定义在 chapter_compose,这里不要重复定义——
# 两份常量会各走各的,改一处另一处不生效。


@dataclass
class PreparedContext:
    """generate_chapter 阶段 1 的产出:写这一章要知道的一切(见 _prepare_chapter_context)。

    字段都是 prompt 实参的直接来源;`compose_context(project)` 是它与
    chapter_compose.ChapterContext 的显式转换——**不在组装时就构造**,因为
    style_block 还要在本阶段末尾追加疲劳词/雷区(顺序有语义)。
    """

    outline: Any
    next_outline: Any
    style_block: str
    rolling: str
    recent: str
    recent_full: list[str]
    handoff_block: str
    hard_constraints: str
    resource_ledger: str
    known_roster: str
    foreshadow_reminders: str
    device_reminders: str
    avoid_repetition: str
    twist_prep: str
    revision_block: str
    premise_block: str = ""
    preflight_issues: list[dict] = field(default_factory=list)

    def compose_context(self, *, project) -> "ChapterContext":
        """转成 Composer 用的上下文(含运行时算出的 deai_rules)。"""
        from app.engines.pipeline.chapter_compose import ChapterContext

        return ChapterContext(
            chapter_number=self.outline.chapter_number,
            outline=self.outline,
            next_outline=self.next_outline,
            style_block=self.style_block,
            rolling=self.rolling,
            recent=self.recent,
            handoff_block=self.handoff_block,
            hard_constraints=self.hard_constraints,
            known_roster=self.known_roster,
            resource_ledger=self.resource_ledger,
            foreshadow_reminders=self.foreshadow_reminders,
            device_reminders=self.device_reminders,
            avoid_repetition=self.avoid_repetition,
            twist_prep=self.twist_prep,
            premise_block=self.premise_block,
            deai_rules=_deai_rules_block(self.recent_full),
            project=project,
        )


async def _prepare_chapter_context(
    db: Session,
    project: Project,
    chapter_number: int,
    *,
    outline: Outline,
    revision: str | None = None,
    tendency: Tendency | None = None,
    report=None,
) -> PreparedContext:
    """阶段 1/4:组装本章写作上下文。纯读 + 确定性推导(唯一 LLM 是写前审核)。

    从 generate_chapter 拆出。这一段的特点是「变量多、依赖散」:文风块被六处
    依次追加(顺序有语义),一致性引擎产四块,再加防复读、反转预备、重写块。
    收成一个数据结构后,后续阶段只认 PreparedContext 的字段名,不必再追闭包。
    """

    def _report(stage: str) -> None:
        if report:
            try:
                report(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

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
    # 故事 DNA(味道锚+故事骨架):题材/口味的正样本锚定 + 情节组织的结构配方
    # (节奏/卡点/桥段)。DNA 未设置时 dna_block_of 返回空串,行为不变。
    style_block += dna_block_of(project.dna)

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
    # 伏笔日程(§1.3):把「到期提醒」升级为硬性任务 + 准入控制。
    # 旧提醒是模型可以无视的一行字;日程是「本章必须兑现 X」的清单——
    # 模型爱埋伏笔不爱收,这是全行业通病,得靠排程而不是靠自觉。
    foreshadow_agenda = build_agenda(
        db, project.id, chapter_number,
        target_chapters=int(project.target_chapters or 0),
    )
    foreshadow_reminders = render_agenda_block(foreshadow_agenda, chapter_number)
    if not foreshadow_reminders:
        # 没有排程任务时回到旧的提醒语义(临近但未到期的伏笔也值得提一句)
        foreshadow_reminders = scheduler.reminder_block(chapter_number)
    # 常驻装置催场(Phase 3):宪法里登记的金手指/信物断档到阈值就点名催场,治
    # 「女主有系统却多章消失」。无 canon 装置 / 老书契约无 devices_present → 空串零影响。
    device_reminders = devices_reminder_block(db, project.id, chapter_number)

    # 反转预备(§1.4):转折章才注入——把「读者此刻相信什么 / 还不知道什么」摆给模型,
    # 它才知道要掀翻什么。非转折章查一次就空串(零 token、零行为变化)。
    # advisory 语义:只喂素材,不设卡口(methodology 见 reader_knowledge 模块头)。
    twist_prep = render_twist_block(
        build_reader_view(db, project.id, chapter_number, outline=outline), db
    )

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
    # 跨章防复读两级:字面级(近几章高频 n-gram/逐句)+ 语义级(桥段台账:前文
    # 已写滥 ≥2 次的母题,带章号与次数注入,治「换措辞复用同一桥段」)。
    avoid_repetition = "\n\n".join(
        b for b in (
            avoid_block(recent_full),
            ledger_avoid_block(db, project.id, chapter_number),
        ) if b
    )

    # 生成端疲劳词表(P5 治本项):最近几章体检出的高频 AI 腔 → 本章草稿的黑名单,
    # 生成时就别写,别全靠事后洗(InkOS 疲劳词表思路)。追加进 style_block,
    # 草稿/定稿/守卫压缩/去味重写全链路都吃得到;全书干净时只有静态黑名单。
    style_block += fatigue_block(recent_full)
    # 雷区清单(作者明令禁止的桥段):同样追加进 style_block——草稿/定稿/守卫/
    # 去味全链路可见,一次标注全书生效(补齐批注跨不了章的缺口)。无雷区 → 空串。
    style_block += banned_block(db, project.id)

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


    # 核心梗块:全书梗卡摘要 + 本章兑现拍。空串 = 无梗卡/未标拍,prompt 零变化。
    from app.db.models import Premise as PremiseModel

    premise_block = ""
    premise_row = (
        db.query(PremiseModel)
        .filter(PremiseModel.project_id == project.id, PremiseModel.kind == "main")
        .first()
    )
    if premise_row is not None and (premise_row.high_concept or "").strip():
        lines = [f"【核心梗(全书的纲,本章正文必须守住并兑现)】"]
        lines.append(f"高概念:{premise_row.high_concept.strip()}")
        bounds = [str(b).strip() for b in (premise_row.boundaries or []) if str(b).strip()]
        if bounds:
            lines.append("边界禁忌(违反即崩梗,情节与对白不得越界):" + "、".join(bounds))
        beat = (getattr(outline, "premise_beat", "") or "").strip()
        if beat:
            lines.append(f"本章兑现:{beat}——正文要让它真实发生,不要一笔带过")
        premise_block = "\n".join(lines) + "\n\n"

    return PreparedContext(
        outline=outline,
        next_outline=next_outline,
        style_block=style_block,
        rolling=rolling,
        recent=recent,
        recent_full=recent_full,
        handoff_block=handoff_block,
        hard_constraints=hard_constraints,
        resource_ledger=resource_ledger,
        known_roster=known_roster,
        foreshadow_reminders=foreshadow_reminders,
        device_reminders=device_reminders,
        avoid_repetition=avoid_repetition,
        twist_prep=twist_prep,
        revision_block=revision_block,
        premise_block=premise_block,
        preflight_issues=preflight_issues,
    )


async def generate_chapter(
    db: Session,
    project: Project,
    chapter_number: int,
    tendency: Tendency | None = None,
    progress=None,
    revision: str | None = None,
) -> tuple[Chapter, list[dict], dict, "GuardResult", dict, list[dict]]:
    """生成一章(编排器):写前审核 → 草稿 → 定稿 → 门禁+精修 → 落库 → 章后链路。

    本函数只做**编排**:四个阶段各自成模块,这里负责按顺序串起来 + 持有跨阶段的
    少数共享状态。要看某一阶段的具体逻辑,去对应模块(docs/14 诊断②:章级单发
    无分解——这次拆解是对该诊断的结构性回应)。

      阶段 1  prepare  `_prepare_chapter_context`(本文件)——上下文组装
      阶段 2  compose  `chapter_compose.Composer`——草稿 → 定稿(31 个占位符)
      阶段 3  rework   `chapter_rework.review_and_rework`——门禁 + 精修回炉
      阶段 4  finalize `chapter_finalize.finalize_and_persist`——守卫/去味/落库

    progress: 可选回调 fn(stage_text),六段各报一次(异步任务进度用)。
    revision: 重写时用户的修改意见;仅当本章已有正文时连同上一版
        (截断)注入草稿 prompt,首次生成传了也会被忽略。

    回炉语义(阶段 3,封顶 review_max_revisions 轮共享预算)与隔离语义
    (阶段 4)的完整说明见两个子模块的模块头 docstring,不在这里重复。

    返回 (Chapter, 一致性门禁问题列表, 抽取统计, 字数守卫结果, 审校结果 dict,
    写前审核警告列表)。quarantined 时抽取统计为空 dict;写前审核警告
    (docs/08 §5.3)severity 一律 major,只警告不阻断,已随落库持久化。
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

    # ================= 阶段 1/4:上下文组装(prepare) =================
    # 组装出「写这一章要知道的一切」。纯读 + 纯确定性推导,不含 LLM 创作调用
    # (唯一的例外是写前审核的一次快模型调用,它是校验性质)。
    ctx = await _prepare_chapter_context(
        db, project, chapter_number, outline=outline,
        revision=revision, tendency=tendency, report=_report,
    )
    preflight_issues = ctx.preflight_issues
    style_block = ctx.style_block
    recent_full = ctx.recent_full

    # ================= 阶段 2/4:生成(get draft + finalize) =================
    # 场景级开关开启时逐场写;否则整章一发。两条路的产物都是 (draft, final),
    # 之后完全同路。回炉轮的重写统一走 Composer(整章一发,理由见其 docstring)。
    scene_result = None
    if project.scene_level_enabled:
        from app.engines.pipeline.scene_chapter import compose_by_scenes

        scene_result = await compose_by_scenes(
            db, project, chapter_number,
            style_block=style_block,
            deai_rules=_deai_rules_block(recent_full),
            rolling_summary=ctx.rolling,
            recent_tail=ctx.recent,
            handoff_block=ctx.handoff_block,
            outline_summary=outline.summary,
            outline_title=outline.title,
            scene_anchor=str(getattr(outline, "scene_anchor", "") or ""),
            threshold=project.review_pass_threshold,
            outline=outline,
            report=_report,
            revision_directive=ctx.revision_block,
        )
        draft = scene_result.text
        # 场景级已有逐场情绪/画面判定,定稿只做「文字层面」的收束(去味诊断 +
        # 配对反例),不再重复判断情节——同一件事两处判必然给出不一致的结论。
        _report("2/6 定稿修订")
        flavor_hits_scene = _flavor_hits_block(ai_flavor_report(draft))
        finalize_prompt_scene = CHAPTER_FINALIZE_PROMPT.format(
            chapter_number=chapter_number,
            chapter_title=outline.title,
            chapter_purpose=outline.chapter_purpose,
            drama_task=_drama_task_block(outline),
            foreshadowing=outline.foreshadowing,
            chapter_summary=outline.summary,
            rolling_summary=ctx.rolling,
            known_roster=ctx.known_roster,
            resource_ledger=ctx.resource_ledger,
            draft_text=draft,
            flavor_hits=flavor_hits_scene,
            style_directives=style_block + pairwise_examples_block(),
        )
        final = _strip_meta(await get_adapter_for(Task.FINALIZE).ask(finalize_prompt_scene))
        logger.info(
            "第 %d 章场景级生成:%d 场(通过 %d,未过 %d),定稿完成",
            chapter_number, scene_result.stats.get("scene_count", 0),
            scene_result.stats.get("accepted", 0), scene_result.stats.get("rejected", 0),
        )
    composer = Composer(
        ctx.compose_context(project=project),
        precomputed=(draft, final) if scene_result is not None else None,
    )
    if scene_result is None:
        logger.info("第 %d 章:生成草稿...", chapter_number)
        draft, final = await composer(
            ctx.revision_block, "1/6 生成草稿", "2/6 定稿修订", report=_report
        )

    # ================= 阶段 3/4:分级回炉(门禁 + 精修) =================
    # 门禁先行(先修对)再精修(再修好);三条退出路径见 chapter_rework 模块头。
    # 场景级逐场验收的统计也并进主审结果(前端生成结果卡展示)。
    outcome = await review_and_rework(
        db, project, chapter_number, draft, final,
        outline=outline, rolling=ctx.rolling, compose=composer, report=_report,
    )
    draft, final = outcome.draft, outcome.final
    review_result = outcome.state.review_result
    if scene_result is not None and scene_result.stats.get("rejected"):
        review_result["scene_stats"] = scene_result.stats
    # ================= 阶段 4/4:收尾(守卫 + 去味 + 落库 + 章后链路) =================
    fin = await finalize_and_persist(
        db, project, chapter_number, outline, final,
        draft=draft,
        review_result=review_result,
        gate_issues=outcome.gate_issues,
        review_degraded=outcome.state.review_degraded,
        reviewed_text=outcome.reviewed_text,
        last_fixed_issues=outcome.state.last_fixed_issues,
        preflight_issues=preflight_issues,
        style_block=style_block,
        report=_report,
    )
    if fin.quarantined:
        return (fin.chapter, outcome.gate_issues, {}, fin.guard_result,
                review_result, preflight_issues)

    # ---- 章后链路(门禁通过才走):抽取写圣经 → 滚动摘要 → 章末契约 ----
    extraction_stats = await apply_chapter_tail(
        db, project, fin.chapter, chapter_number, fin.final_text, outline.title,
        report=_report,
    )

    # ---- 重写场景:下游章节的滚动摘要基于旧文,重建 ----
    # 文风备忘:随书累积"这本书怎么写"(与摘要互补),注入后续章草稿;
    # flavor_notes = 本章 AI 味体检的高频类别(病灶回流):沉淀进备忘
    # 「要避开的」小节,下一章草稿的黑名单由此长出本书特有的部分。
    _report("文风备忘更新")
    await update_style_memo(
        db, project, chapter_number, fin.final_text,
        flavor_notes=memo_notes_block(fin.deai_report),
    )

    rebuilt = await rebuild_summaries_after(db, project, chapter_number, progress)
    if rebuilt:
        logger.info("第 %d 章重写,已重建下游摘要: %s", chapter_number, rebuilt)

    logger.info("第 %d 章完成,共 %d 字。", chapter_number, fin.chapter.word_count)
    return (fin.chapter, outcome.gate_issues, extraction_stats, fin.guard_result,
            review_result, preflight_issues)
