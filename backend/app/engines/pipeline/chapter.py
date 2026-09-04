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

from app.db.models import (
    Chapter,
    ChapterVersion,
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
from app.engines.consistency.checker import (
    blockers_of,
    blocker_fingerprint,
    check_chapter,
    continuity_score,
    persist_issues,
    triage_issues,
)
from app.engines.consistency.preflight import preflight_chapter
from app.engines.consistency.repetition import avoid_block, dedup_paragraphs
from app.engines.consistency.motifs import banned_block, ledger_avoid_block
from app.engines.pipeline.handoff import load_handoff_block
from app.engines.devices import devices_reminder_block
from app.engines.polish import ai_flavor_report
from app.engines.polish.polisher import (
    _flavor_hits_block,
    deai_self_heal,
    fatigue_block,
    memo_notes_block,
)
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
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import (
    CHAPTER_DRAFT_PROMPT,
    CHAPTER_FINALIZE_PROMPT,
)
from app.engines.pipeline.word_guard import GuardResult, word_count_guard
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapter")

_REVISION_EXCERPT_CHARS = 1500  # 重写时上一版正文注入草稿 prompt 的截断长度

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
            word_floor=project.target_words_per_chapter * 4 // 5,
            word_ceil=project.target_words_per_chapter * 6 // 5,
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
    chapter.status = "quarantined" if blockers else "pending_review"
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
    # 文风备忘:随书累积"这本书怎么写"(与摘要互补),注入后续章草稿;
    # flavor_notes = 本章 AI 味体检的高频类别(病灶回流):沉淀进备忘
    # 「要避开的」小节,下一章草稿的黑名单由此长出本书特有的部分。
    _report("文风备忘更新")
    await update_style_memo(
        db, project, chapter_number, final,
        flavor_notes=memo_notes_block(_deai_after),
    )

    rebuilt = await rebuild_summaries_after(db, project, chapter_number, progress)
    if rebuilt:
        logger.info("第 %d 章重写,已重建下游摘要: %s", chapter_number, rebuilt)

    logger.info("第 %d 章完成,共 %d 字。", chapter_number, chapter.word_count)
    return chapter, gate_issues, extraction_stats, guard_result, review_result, preflight_issues


