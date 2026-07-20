# app/engines/polish/polisher.py
# -*- coding: utf-8 -*-
"""润色主流程:抽事实锁定 → 润色(带去AI味规则)→ 事实校验 → AI味对比。

流程(docs/03-engines.md 引擎 C):
1. FACT_LOCK:从原文抽"改了就算改剧情"的硬事实清单
2. POLISH:带锁定清单 + 用户风格标签 + 去AI味规则,生成润色稿
3. FACT_VERIFY:对照清单校验润色稿,发现违规 → 报告给用户(不自动回滚)
4. AI 味前后对比(纯规则,零成本)
"""
from __future__ import annotations

import logging

from app.engines.consistency.extractor import parse_llm_json
from app.engines.polish.ai_flavor import ai_flavor_report
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts.polish import (
    _DEAI_RULES,
    FACT_LOCK_PROMPT,
    FACT_VERIFY_PROMPT,
    FRAGMENT_POLISH_PROMPT,
    POLISH_PROMPT,
)
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.polish")

_MAX_POLISH_CHARS = 12000
_MAX_FRAGMENT_CHARS = 2000


async def polish_fragment(
    fragment: str,
    direction: str = "",
    chapter_summary: str = "",
) -> dict:
    """润色阅读时点选的单个段落(轻量单次调用,带用户润色方向)。

    与 polish_text 的事实锁定三段式不同,片段很短,直接靠 prompt 铁律
    约束"只改文笔不改情节",省去事实抽取/校验两轮调用。
    返回 {polished, notes}。
    """
    fragment = fragment.strip()
    if not fragment:
        raise ValueError("待润色片段为空")
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        raise ValueError(
            f"片段最长 {_MAX_FRAGMENT_CHARS} 字,当前 {len(fragment)} 字"
        )

    polished = await get_adapter_for(Task.POLISH).ask(
        FRAGMENT_POLISH_PROMPT.format(
            chapter_summary=chapter_summary.strip() or "(无)",
            direction=direction.strip() or "整体更自然流畅",
            fragment=fragment,
        )
    )
    polished = polished.strip()
    if not polished:
        raise ValueError("模型未返回润色结果,请重试")
    return {"polished": polished, "notes": None}


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

    # ---- 1. 抽事实清单(锁定) ----
    logger.info("润色 1/3:抽取情节事实...")
    fact_adapter = get_adapter_for(Task.FACT_EXTRACT)
    facts_raw = await fact_adapter.ask(FACT_LOCK_PROMPT.format(text=text))
    locked_facts: list[str] = [
        str(f) for f in (parse_llm_json(facts_raw).get("facts") or [])
    ][:15]
    facts_block = "\n".join(f"{i+1}. {f}" for i, f in enumerate(locked_facts)) or "(未抽出,凭正文自查)"

    # ---- 2. 润色 ----
    logger.info("润色 2/3:生成润色稿(锁定 %d 条事实)...", len(locked_facts))
    polished = await get_adapter_for(Task.POLISH).ask(
        POLISH_PROMPT.format(
            locked_facts=facts_block,
            text=text,
            style_directives=style_block,
            deai_rules=_DEAI_RULES,
        )
    )
    polished = polished.strip()

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
    before = ai_flavor_report(text)
    after = ai_flavor_report(polished)

    return {
        "polished": polished,
        "locked_facts": locked_facts,
        "violations": violations,
        "flavor_before": {"score": before.score, "hits": before.hits,
                          "summary": before.summary()},
        "flavor_after": {"score": after.score, "hits": after.hits,
                         "summary": after.summary()},
    }
