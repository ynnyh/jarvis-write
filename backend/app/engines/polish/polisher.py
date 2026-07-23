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

from app.engines.consistency.extractor import parse_llm_json
from app.engines.polish.ai_flavor import FlavorReport, ai_flavor_report
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.base import LLMAdapter, LLMMessage
from app.llm.router import Task, get_adapter_for
from app.prompts.polish import (
    _DEAI_RULES,
    _OUTPUT_CONTRACT,
    DISCUSS_SUGGESTION_MARK,
    DISCUSS_SYSTEM_PROMPT,
    FACT_LOCK_PROMPT,
    FACT_VERIFY_PROMPT,
    FRAGMENT_POLISH_PROMPT,
    POLISH_PROMPT,
)
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.polish")

_MAX_POLISH_CHARS = 12000
_MAX_FRAGMENT_CHARS = 2000
_MAX_HITS_IN_PROMPT = 10  # 贴进 prompt 的命中点上限,防 token 膨胀


def _flavor_hits_block(report: FlavorReport) -> str:
    """把检测命中明细渲染成 prompt 块:类别 + 命中套话 + 命中句。"""
    if not report.hits:
        return "(本次规则检测未命中明显 AI 腔,按通用去 AI 腔规则自查)"
    lines = []
    for h in report.hits[:_MAX_HITS_IN_PROMPT]:
        sent = h.sentence if len(h.sentence) <= 60 else h.sentence[:60] + "……"
        lines.append(f"- [{h.category}] 命中「{h.phrase}」:{sent}")
    return "\n".join(lines)


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
) -> dict:
    """润色阅读时点选的单个段落(轻量单次调用,带用户润色方向)。

    与 polish_text 的事实锁定三段式不同,片段很短,直接靠 prompt 铁律
    约束"只改文笔不改情节",省去事实抽取/校验两轮调用。
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
            output_contract=_OUTPUT_CONTRACT,
        )
    )
    polished, diagnosis = _split_polish_output(raw)
    if not polished:
        raise ValueError("模型未返回润色结果,请重试")
    return {"polished": polished, "notes": diagnosis}


async def polish_text(
    text: str,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
) -> dict:
    """润色一段文本。

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
) -> dict:
    """就选中段落与作者多轮对话:可解释、可给改写建议。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - target:作者选中、正在讨论的段落原文。
    - chapter_summary / before / after:上下文(本章梗概、选段上下文),仅供理解。

    返回 {reply, suggestion};suggestion 非空时前端浮出「采用此改写」按钮,
    复用与润色相同的替换+同步链路。单次调用,复用 inspire chat 的多轮范式。
    """
    target = target.strip()
    if not target:
        raise ValueError("选中的段落为空")

    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_DISCUSS_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    system = DISCUSS_SYSTEM_PROMPT.format(
        chapter_summary=chapter_summary.strip() or "(无)",
        before=before.strip()[-_MAX_DISCUSS_CONTEXT_CHARS:] or "(无)",
        target=target,
        after=after.strip()[:_MAX_DISCUSS_CONTEXT_CHARS] or "(无)",
        mark=DISCUSS_SUGGESTION_MARK,
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


async def _discuss_complete(adapter: LLMAdapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:带空回复重试 + 用量记账(对齐 ask 的兜底)。"""
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
