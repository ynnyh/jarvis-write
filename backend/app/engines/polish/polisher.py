# app/engines/polish/polisher.py
# -*- coding: utf-8 -*-
"""润色主流程:抽事实锁定 → 润色(检测驱动去AI味)→ 事实校验 → AI味对比。

流程(docs/03-engines.md 引擎 C):
1. FACT_LOCK:从原文抽"改了就算改剧情"的硬事实清单
2. POLISH:带锁定清单 + 用户风格标签 + AI 味检测命中点,先诊断后治疗生成润色稿
3. FACT_VERIFY:对照清单校验润色稿,发现违规 → 报告给用户(不自动回滚)
4. AI 味前后对比(纯规则,零成本)

检测驱动定点改写:润色前先跑 ai_flavor_report,把命中句+类别贴进 prompt,
"针对这些具体命中点修改,其余好句子保持";输出为【诊断】→【策略】→【润色稿】
两段式契约,解析出润色稿,诊断留存(片段润色返回给前端展示)。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable

from app.engines.consistency.extractor import parse_llm_json
from app.engines.polish.ai_flavor import (
    _RULES,
    FlavorReport,
    ai_flavor_report,
    gate_override,
)
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block, voice_block_of
from app.engines.tendency.cards import render_cards_block
from app.llm.base import LLMAdapter, LLMMessage, complete_text_with_budget
from app.llm.router import Task, get_adapter_for
from app.prompts.polish import (
    _DEAI_RULES,
    _OUTPUT_CONTRACT,
    CRAFT_MODES,
    DEAI_REWRITE_PROMPT,
    DISCUSS_CHAPTER_SYSTEM_PROMPT,
    DISCUSS_SUGGESTION_MARK,
    DISCUSS_SYSTEM_PROMPT,
    FACT_LOCK_PROMPT,
    FACT_VERIFY_PROMPT,
    FRAGMENT_CRAFT_PROMPT,
    FRAGMENT_POLISH_PROMPT,
    POLISH_PROMPT,
)
from app.prompts.style_capsules import pairwise_examples_block
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.polish")

_MAX_POLISH_CHARS = 12000
_MAX_FRAGMENT_CHARS = 2000
_MAX_HITS_IN_PROMPT = 10  # 贴进 prompt 的命中点上限,防 token 膨胀

# AI 味自愈门槛:定稿终版 score 超过此值触发定向去味重写。score = 每千字加权命中 +
# 统计罚分,干净文本通常 <5,套话偏多或有节奏/结构问题会到 6+。实测可调。
# 管理端可通过 ai_flavor_config 在线热更(覆盖值优先生效,不重启)。
DEAI_GATE_SCORE = 6.0


def get_deai_gate() -> float:
    """当前生效的自愈门槛:热更覆盖优先,无覆盖回落代码常量。"""
    return gate_override() if gate_override() is not None else DEAI_GATE_SCORE


# 自愈重写的安全阀:重写稿相对原文的篇幅比,越界(缩水/膨胀过多)视为跑偏并丢弃
_DEAI_LEN_LO, _DEAI_LEN_HI = 0.75, 1.25

# ---- 第二裁判容差(复合验收):统计判据组倒退超过容差即判"改坏了" ----
# 借鉴 stop-slop 五维评分思路:正则分(第一裁判)下降还不够,词汇丰富度/
# 新颖率被洗没、虚词「的」字密度反而升高,说明把人味也洗掉了,不采纳。
_JUDGE_TOL = {"ttr": 0.05, "novelty": 0.05, "de": 0.015, "func": 0.02}


def judges_regress(before: FlavorReport, after: FlavorReport) -> bool:
    """第二裁判:重写稿的统计判据组是否相对原稿倒退(Writer↔Critic 的 Critic)。

    只做同篇幅文本的前后对比;任一指标为 None(短文本)时不参与判断。
    """
    b, a = before.metrics, after.metrics
    if b.get("ttr") is None or a.get("ttr") is None:
        return False
    if a["ttr"] < b["ttr"] - _JUDGE_TOL["ttr"]:
        return True
    if (a.get("novelty_4gram") or 1.0) < (b.get("novelty_4gram") or 1.0) - _JUDGE_TOL["novelty"]:
        return True
    if (a.get("de_ratio") or 0.0) > (b.get("de_ratio") or 0.0) + _JUDGE_TOL["de"]:
        return True
    if (a.get("funcword_density") or 0.0) > (b.get("funcword_density") or 0.0) + _JUDGE_TOL["func"]:
        return True
    return False


def _flavor_hits_block(report: FlavorReport) -> str:
    """把检测命中明细渲染成 prompt 块:类别 + 命中套话 + 命中句。"""
    if not report.hits:
        return "(本次规则检测未命中明显 AI 腔,按通用去 AI 腔规则自查)"
    lines = []
    for h in report.hits[:_MAX_HITS_IN_PROMPT]:
        sent = h.sentence if len(h.sentence) <= 60 else h.sentence[:60] + "……"
        dlg = "(对白,谨慎:保人物声线,仅当确属 AI 腔才动)" if h.dialogue else ""
        lines.append(f"- [{h.category}]{dlg} 命中「{h.phrase}」:{sent}")
    return "\n".join(lines)


def _strip_rewrite_meta(raw: str) -> str:
    """去掉模型可能加的 markdown 围栏与首尾空白(定向重写只要正文)。"""
    s = (raw or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


# =============== 生成端疲劳词表(P5 治本项,InkOS 疲劳词表思路) ===============
# 事后洗(自愈)是补救,生成时就别写才是治本:静态黑名单(全站高危套话)+
# 动态病灶(本书最近几章实际高频的类别)。克制原则(Humanizer-zh 的教训):
# 只列 top_k 类,规则全塞会把正文逼成避雷安全文,反而更 AI。
_FATIGUE_STATIC = (
    "【本章禁写的高危句式(出现即返工)】\n"
    "- 神态套话:眼中闪过一丝/嘴角勾起/眸光一沉/空气仿佛凝固/命运的齿轮/不禁、下意识地\n"
    "- 稳妥套话:沉默片刻/微微一笑/轻声说道/缓缓开口/淡淡地道\n"
    "- 欧化句式:随着…的发展/扮演着…角色/不仅仅是…而是/标志着…/对于…而言/被赋予了\n"
    "- 总结升华:综上所述/总而言之/段尾点题说教\n"
    "- 情绪直喊:他感到无比绝望/心中五味杂陈 —— 情绪一律用动作、细节、对话呈现\n"
)


def fatigue_block(recent_texts: list[str], top_k: int = 3) -> str:
    """生成端疲劳词表:静态黑名单 + 最近几章体检出的本书特有高频 AI 腔。

    recent_texts: 最近几章的定稿正文(生成下一章草稿前传入);
    空列表/全书干净时只给静态黑名单。
    """
    agg: dict[str, dict] = {}
    for t in recent_texts:
        for name, cat in ai_flavor_report(t).categories.items():
            slot = agg.setdefault(name, {"count": 0, "weight": cat["weight"]})
            slot["count"] += cat["count"]
    lines = ["" + _FATIGUE_STATIC]
    top = sorted(agg.items(), key=lambda kv: -kv[1]["count"] * kv[1]["weight"])[:top_k]
    if top:
        detail = "、".join(
            f"{name}×{v['count']}(如:{_category_samples(name)})" for name, v in top
        )
        lines.append(
            "【本书最近几章反复出现的 AI 腔(本章严禁再犯,改用具体动作/细节/对话)】\n"
            f"- {detail}\n"
        )
    return "\n".join(lines)


def _category_samples(name: str) -> str:
    """某类别的高频命中套话样例(取检测规则的规则名/短语,静态即可)。"""
    for cat, _w, rules in _RULES:
        if cat == name:
            return "、".join(label or "…" for _p, label in rules[:3])
    return "…"


def memo_notes_block(report: FlavorReport) -> str:
    """病灶回流文风备忘:本章体检出的高频类别 → 备忘「要避开的」小节的素材。

    干净章节返回空串(prompt 里整块省略,与 advice_block 同一套约定)。
    """
    if not report.categories:
        return ""
    top = sorted(report.categories.items(), key=lambda kv: -kv[1]["count"])[:3]
    detail = "、".join(f"{k}×{v['count']}" for k, v in top)
    return (
        "【本章 AI 味体检(规则检测,供备忘第 5 节「要避开的」沉淀)】\n"
        f"- {detail}\n"
        "把上述本书反复出现的腔调沉淀进「要避开的」小节(具体到句式名),"
        "后续章草稿会以此为黑名单。\n"
    )


async def deai_rewrite(text: str, report: FlavorReport, style_block: str = "") -> str:
    """定向去味重写:把命中句 + 统计诊断(advice)+ 配对反例 + 文风范本喂给强模型,
    只改问题处、其余原样,直接返回去味后的完整正文。走 Task.FINALIZE。"""
    prompt = DEAI_REWRITE_PROMPT.format(
        flavor_hits=_flavor_hits_block(report),
        advice_block=report.advice_block(),
        pairwise=pairwise_examples_block(),
        style_directives=style_block,
        draft_text=text,
    )
    raw = await get_adapter_for(Task.FINALIZE).ask(prompt)
    return _strip_rewrite_meta(raw)


async def deai_self_heal(
    text: str,
    style_block: str = "",
    threshold: float | None = None,
    max_rounds: int = 2,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, FlavorReport, FlavorReport]:
    """AI 味自愈闭环:体检 → 超标定向重写 → 复测 → 限轮收敛。

    返回 (最终正文, 初检报告, 末检报告)。带安全阀,只在"确实改好了"时才采纳:
    - 重写调用异常 / 输出为空 → 丢弃本轮,保留当前最好版本
    - 篇幅越界(缩水或膨胀超阈值)→ 视为跑偏,丢弃本轮
    - score 未下降 → 没改好,丢弃本轮
    - 复合验收:score 降了但统计判据组倒退(judges_regress,把人味洗没了)→ 丢弃本轮
    任一轮丢弃即停(不硬撑),绝不返回比原文更差的版本。
    threshold 不传时读热更门槛(get_deai_gate,管理端可在线调)。
    """
    if threshold is None:
        threshold = get_deai_gate()
    before = ai_flavor_report(text)
    if before.score <= threshold:
        return text, before, before

    best_text, best_report = text, before
    for i in range(max_rounds):
        if progress:
            try:
                progress(f"AI 味自愈(第 {i + 1} 轮,当前 {best_report.score:.1f})")
            except Exception:  # noqa: BLE001 — 进度上报绝不影响主流程
                pass
        try:
            rewritten = await deai_rewrite(best_text, best_report, style_block)
        except Exception:  # noqa: BLE001 — LLM 失败不该毁掉已定稿的正文
            logger.warning("去味重写调用失败,保留当前版本", exc_info=True)
            break
        if not rewritten:
            break
        ratio = len(rewritten) / max(len(best_text), 1)
        if not (_DEAI_LEN_LO <= ratio <= _DEAI_LEN_HI):
            logger.info("去味重写篇幅越界(比例 %.2f),丢弃本轮", ratio)
            break
        after = ai_flavor_report(rewritten)
        if after.score >= best_report.score:
            logger.info("去味重写未降分(%.1f→%.1f),丢弃本轮", best_report.score, after.score)
            break
        if judges_regress(best_report, after):
            logger.info(
                "去味重写统计判据倒退(正则分 %.1f→%.1f 但人味指标变差),丢弃本轮",
                best_report.score, after.score,
            )
            break
        best_text, best_report = rewritten, after
        logger.info("去味重写第 %d 轮:score → %.1f", i + 1, best_report.score)
        if best_report.score <= threshold:
            break
    return best_text, before, best_report


def _split_polish_output(raw: str) -> tuple[str, str | None]:
    """解析两段式输出,返回 (润色稿, 诊断)。模型未按契约输出时整段当润色稿。"""
    m = re.search(r"【润色稿】", raw)
    if not m:
        return raw.strip(), None
    polished = raw[m.end():].strip()
    diagnosis = raw[:m.start()].strip() or None
    return polished, diagnosis


async def polish_fragment(
    fragment: str,
    direction: str = "",
    chapter_summary: str = "",
    voice_block: str = "",
) -> dict:
    """润色阅读时点选的单个段落(轻量单次调用,带用户润色方向)。

    与 polish_text 的事实锁定三段式不同,片段很短,直接靠 prompt 铁律
    约束"只改文笔不改情节",省去事实抽取/校验两轮调用。
    voice_block:文风范本(去 AI 味正向锚,API 层从创作偏好档案取;引擎侧默认空)。
    返回 {polished, notes};notes 为两段式输出的诊断部分(可为 None)。
    """
    fragment = fragment.strip()
    if not fragment:
        raise ValueError("待润色片段为空")
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        raise ValueError(
            f"片段最长 {_MAX_FRAGMENT_CHARS} 字,当前 {len(fragment)} 字"
        )

    raw = await get_adapter_for(Task.POLISH).ask(
        FRAGMENT_POLISH_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            direction=direction.strip() or "整体更自然流畅",
            flavor_hits=_flavor_hits_block(ai_flavor_report(fragment)),
            fragment=fragment,
            voice_block=voice_block,
            output_contract=_OUTPUT_CONTRACT,
        )
    )
    polished, diagnosis = _split_polish_output(raw)
    if not polished:
        raise ValueError("模型未返回润色结果,请重试")
    return {"polished": polished, "notes": diagnosis}


_IDEA_BULLET_RE = re.compile(r"^(?:[-*•]\s*|\d+[.、)]\s*)")


def _parse_ideas(raw: str) -> list[str]:
    """把 brainstorm 的逐行点子解析成列表:去行首「- 」「1. 」「1、」等标记,去空行,最多 6 条。
    模型没分行(整段)时退化为单条,前端仍可展示。"""
    ideas: list[str] = []
    for line in raw.splitlines():
        s = _IDEA_BULLET_RE.sub("", line.strip()).strip()
        if s:
            ideas.append(s)
    return ideas[:6] if ideas else [raw.strip()]


async def craft_fragment(
    fragment: str,
    mode: str,
    note: str = "",
    chapter_summary: str = "",
    voice_block: str = "",
) -> dict:
    """选区 craft 微工具(阅读/写作时对选中片段的单次调用):
    - describe/expand:在"不推进新剧情、不新增事件/人物"的前提下补描写 / 扩篇幅,
      返回 {rewrite} 走前端 diff→接受→子串写回(与润色同一替换链路);
    - brainstorm:围绕这段给 3-5 条点子,返回 {ideas},不改正文。
    voice_block:文风范本(去 AI 味正向锚);仅 describe/expand 注入(brainstorm 不改正文)。
    返回 {mode, rewrite, ideas, notes}(rewrite/ideas 按模式二选一,另一个为 None)。
    """
    fragment = fragment.strip()
    if not fragment:
        raise ValueError("待处理片段为空")
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        raise ValueError(
            f"片段最长 {_MAX_FRAGMENT_CHARS} 字,当前 {len(fragment)} 字"
        )
    spec = CRAFT_MODES.get(mode)
    if spec is None:
        raise ValueError(f"未知的 craft 模式:{mode}")

    raw = await get_adapter_for(Task.POLISH).ask(
        FRAGMENT_CRAFT_PROMPT.format(
            intro=spec["intro"],
            chapter_summary=chapter_summary.strip() or "(无)",
            note=note.strip() or "(无)",
            fragment=fragment,
            rules=spec["rules"],
            # describe/expand 追加去 AI 腔规则 + 文风范本(新增文字也要干净、也要对味);
            # brainstorm 不改正文,两者都不注入
            deai_rules=("\n" + _DEAI_RULES + voice_block) if mode != "brainstorm" else "",
            contract=spec["contract"],
        )
    )
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("模型未返回结果,请重试")
    if mode == "brainstorm":
        return {"mode": mode, "rewrite": None, "ideas": _parse_ideas(raw), "notes": None}
    return {"mode": mode, "rewrite": raw, "ideas": None, "notes": None}


async def polish_text(
    text: str,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    directive: str = "",
    cards: Iterable[Any] | None = None,
) -> dict:
    """润色一段文本。

    directive: 用户的自定义修改要求(批量重润时透传),注入 prompt,
    优先级低于润色铁律(仍不改情节)。
    cards: 本书启用的写作手法卡(WritingCard 列表),拼成块追加到风格约束;
    无项目上下文的通用润色端点传 None。
    返回 {polished, locked_facts, violations, flavor_before, flavor_after}。
    """
    text = text.strip()
    if not text:
        raise ValueError("待润色文本为空")
    if len(text) > _MAX_POLISH_CHARS:
        raise ValueError(
            f"单次润色最长 {_MAX_POLISH_CHARS} 字,当前 {len(text)} 字,请分段"
        )

    assembled = assemble_tendency("polish", tendency, global_tendency)
    style_block = render_style_block(assembled)
    # 写作手法卡:作者为本书启用的写法技巧,追加到风格约束块(同 style_memo 套路,
    # 不新增模板占位符)。手法卡是软约束,润色铁律与情节事实仍优先。
    style_block += render_cards_block(cards)
    # 文风范本(去 AI 味正向锚):档案里选的名家/预设 + 范文,润色时也照此语感改
    style_block += voice_block_of(global_tendency)

    # ---- 0. AI 味检测(纯规则,零成本):命中点供定点改写,报告供前后对比 ----
    before = ai_flavor_report(text)

    # ---- 1. 抽事实清单(锁定) ----
    logger.info("润色 1/3:抽取情节事实...")
    fact_adapter = get_adapter_for(Task.FACT_EXTRACT)
    facts_raw = await fact_adapter.ask(FACT_LOCK_PROMPT.format(text=text))
    locked_facts: list[str] = [
        str(f) for f in (parse_llm_json(facts_raw).get("facts") or [])
    ][:15]
    facts_block = "\n".join(f"{i+1}. {f}" for i, f in enumerate(locked_facts)) or "(未抽出,凭正文自查)"

    # ---- 2. 润色(检测驱动:命中点贴进 prompt;两段式先诊断后治疗) ----
    logger.info(
        "润色 2/3:生成润色稿(锁定 %d 条事实,AI 腔命中 %d 处)...",
        len(locked_facts), len(before.hits),
    )
    raw = await get_adapter_for(Task.POLISH).ask(
        POLISH_PROMPT.format(
            locked_facts=facts_block,
            flavor_hits=_flavor_hits_block(before),
            text=text,
            style_directives=style_block,
            deai_rules=_DEAI_RULES,
            output_contract=_OUTPUT_CONTRACT,
            user_directive=(
                f"【用户修改要求(优先满足,但不得违反上述润色铁律)】\n{directive.strip()}\n"
                if directive.strip() else ""
            ),
        )
    )
    polished, diagnosis = _split_polish_output(raw)
    if diagnosis:
        logger.info("润色诊断: %s", diagnosis[:300])

    # ---- 3. 事实校验(兜底) ----
    violations: list[dict] = []
    if locked_facts:
        logger.info("润色 3/3:校验事实完整性...")
        try:
            verify_raw = await fact_adapter.ask(
                FACT_VERIFY_PROMPT.format(
                    locked_facts=facts_block, polished_text=polished
                )
            )
            violations = [
                v
                for v in (parse_llm_json(verify_raw).get("violations") or [])
                if isinstance(v, dict)
            ]
        except Exception as exc:  # noqa: BLE001 — 校验失败不阻塞,标注即可
            logger.warning("事实校验失败: %s", exc)
            violations = [{"fact": "(校验环节异常)", "problem": str(exc)[:200]}]

    if violations:
        logger.warning("润色发现 %d 处事实违规", len(violations))

    # ---- 4. AI 味前后对比(纯规则,零成本) ----
    after = ai_flavor_report(polished)

    return {
        "polished": polished,
        "locked_facts": locked_facts,
        "violations": violations,
        "flavor_before": before.to_dict(),
        "flavor_after": after.to_dict(),
    }


_MAX_DISCUSS_TURNS = 40
_MAX_DISCUSS_MSG_LEN = 2000
_MAX_DISCUSS_CONTEXT_CHARS = 1200  # 上/下文各截断,防 token 膨胀
_MAX_DISCUSS_CHAPTER_CHARS = 3000  # 无选段自由问答:整章正文注入 system 时截断


def _split_discuss_output(raw: str) -> tuple[str, str | None]:
    """解析对话输出,返回 (回复正文, 改写建议或 None)。

    模型想改写这段时,会在【改写建议】标记后给出完整段落(独占其后正文);
    没有该标记就是纯解释/问答。回复正文永远保留标记之前的内容,让作者看到
    模型「打算怎么改」的那一两句说明。
    """
    m = re.search(re.escape(DISCUSS_SUGGESTION_MARK), raw)
    if not m:
        return raw.strip(), None
    reply = raw[: m.start()].strip()
    suggestion = raw[m.end():].strip() or None
    return reply, suggestion


async def discuss_fragment(
    messages: list[dict],
    target: str,
    *,
    chapter_summary: str = "",
    before: str = "",
    after: str = "",
    chapter_excerpt: str = "",
) -> dict:
    """就选中段落与作者多轮对话:可解释、可给改写建议。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - target:作者选中、正在讨论的段落原文;置空 = 无选段的整章自由问答
      (AI 窄栏「随便问」),此时只答不改,不产出改写建议。
    - chapter_summary / before / after:上下文(本章梗概、选段上下文),仅供理解。
    - chapter_excerpt:无选段时注入的整章正文(截断注入,防 token 膨胀)。

    返回 {reply, suggestion};suggestion 非空时前端浮出「采用此改写」按钮,
    复用与润色相同的替换+同步链路。单次调用,复用 inspire chat 的多轮范式。
    """
    target = target.strip()

    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_DISCUSS_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    if target:
        system = DISCUSS_SYSTEM_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            before=before.strip()[-_MAX_DISCUSS_CONTEXT_CHARS:] or "(无)",
            target=target,
            after=after.strip()[:_MAX_DISCUSS_CONTEXT_CHARS] or "(无)",
            mark=DISCUSS_SUGGESTION_MARK,
        )
    else:
        # 整章自由问答:只答不改(无选段锚点,建议无处落)
        system = DISCUSS_CHAPTER_SYSTEM_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            chapter_excerpt=chapter_excerpt[:_MAX_DISCUSS_CHAPTER_CHARS] or "(本章还没有正文)",
        )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_DISCUSS_MSG_LEN])
        for m in turns
    ]

    adapter = get_adapter_for(Task.POLISH)
    reply_raw = await _discuss_complete(adapter, chat_messages)
    reply, suggestion = _split_discuss_output(reply_raw)
    if not reply and not suggestion:
        raise ValueError("模型没有回应,请重试")
    return {"reply": reply, "suggestion": suggestion}


async def discuss_fragment_stream(
    messages: list[dict],
    target: str,
    *,
    chapter_summary: str = "",
    before: str = "",
    after: str = "",
    chapter_excerpt: str = "",
):
    """流式版 discuss_fragment(SSE 打字机):逐字产出 reply,末尾给出结构化 suggestion。

    产出 (kind, payload):
      ("token", str)  reply 的增量文字(【改写建议】标记之后的内容不吐字,归到 suggestion)
      ("done", {"reply": str, "suggestion": str | None})  权威收尾(前端据此收敛气泡)
    入参校验与 system 构造同 discuss_fragment(同步孪生);流式路径不重试空回复、不记账
    (与 openai_compatible._complete_via_stream 的取舍一致:流式拿不到准确用量,可接受)。
    """
    target = target.strip()
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_DISCUSS_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    if target:
        system = DISCUSS_SYSTEM_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            before=before.strip()[-_MAX_DISCUSS_CONTEXT_CHARS:] or "(无)",
            target=target,
            after=after.strip()[:_MAX_DISCUSS_CONTEXT_CHARS] or "(无)",
            mark=DISCUSS_SUGGESTION_MARK,
        )
    else:
        system = DISCUSS_CHAPTER_SYSTEM_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            chapter_excerpt=chapter_excerpt[:_MAX_DISCUSS_CHAPTER_CHARS] or "(本章还没有正文)",
        )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_DISCUSS_MSG_LEN])
        for m in turns
    ]

    adapter = get_adapter_for(Task.POLISH)
    # 边收边按【改写建议】切:标记前是 reply(吐字),标记后是改写正文(存起来,不进聊天气泡)。
    # 未命中标记时,尾部留 len(mark)-1 个字不吐,防标记被拆在两个 chunk 之间而漏字/漏切。
    mark = DISCUSS_SUGGESTION_MARK
    full = ""
    flushed = 0
    hit = False
    async for delta in adapter.stream(chat_messages):
        if not delta:
            continue
        full += delta
        if hit:
            continue
        pos = full.find(mark)
        if pos >= 0:
            if pos > flushed:
                yield ("token", full[flushed:pos])
            flushed = pos
            hit = True
        else:
            safe = len(full) - (len(mark) - 1)
            if safe > flushed:
                yield ("token", full[flushed:safe])
                flushed = safe
    # 循环结束:未命中标记则把尾缓冲(防拆断留的最后几字)补吐完,让打字机不落尾;
    # 命中标记则 flushed 之后都是改写正文,归 suggestion,不吐字。
    if not hit and len(full) > flushed:
        yield ("token", full[flushed:])
    # 权威切分收尾:前端据此把逐字拼出的气泡收敛成最终 reply(消除分帧差异)
    reply, suggestion = _split_discuss_output(full)
    if not reply and not suggestion:
        raise ValueError("模型没有回应,请重试")
    yield ("done", {"reply": reply, "suggestion": suggestion})


async def _discuss_complete(adapter: LLMAdapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:空正文放大预算重试 + 用量记账。

    别在这里自己写"空串就翻倍"的循环:complete() 遇空正文是抛 EmptyContentError,
    统一交给 complete_text_with_budget 处理(见 llm/base.py)。
    """
    return await complete_text_with_budget(adapter, messages)
