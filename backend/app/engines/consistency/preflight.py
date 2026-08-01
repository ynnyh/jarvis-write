# app/engines/consistency/preflight.py
# -*- coding: utf-8 -*-
"""写前审核(Pre-flight Check,docs/08 §5.3)。

生成主流程取大纲后、草稿调用前跑一次(快模型档,一次调用):
本章蓝图(标题/简述/节拍)vs 上一章章末交接契约,找"动笔前就看得出的矛盾"
——典型如蓝图写清晨出发,而上章契约是深夜刚入睡且未提时间跳跃;
或蓝图出场角色与上章章末状态(受伤/离场/不在场)冲突。

产出:警告列表 [{severity:"major", type, description, evidence, suggestion}],
severity 一律 major(只警告不阻断,类型约定 state|timeline)。

降级(不阻塞生成):
- 上一章无有效契约(第一章/老章节未提取/正文指纹失效)→ 跳过,返回 [];
- LLM 调用失败 / JSON 解析失败 → 告警留痕,返回 []。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Outline
from app.engines.consistency.extractor import parse_llm_json
from app.engines.pipeline.handoff import load_handoff_block
from app.engines.timeline import timeline_block
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import PREFLIGHT_CHECK_PROMPT

logger = logging.getLogger("jarvis-write.preflight")

_TYPES = {"state", "timeline"}
_MAX_BEATS = 12  # 节拍注入上限,防蓝图膨胀


def _blueprint_block(outline: Outline) -> str:
    """把本章蓝图渲染成写前审核的对照材料(标题/目的/简述/节拍/出场/场景)。"""
    lines = [
        f"标题:{outline.title}",
        f"本章目的:{outline.chapter_purpose}",
        f"本章简述:{outline.summary}",
    ]
    beats = [str(b).strip() for b in (outline.beats or []) if str(b).strip()]
    if beats:
        lines.append("场景节拍:")
        lines.extend(f"  {i}. {b}" for i, b in enumerate(beats[:_MAX_BEATS], 1))
    chars = "、".join(map(str, outline.characters_involved or []))
    if chars:
        lines.append(f"出场角色:{chars}")
    if outline.scene_location:
        lines.append(f"场景地点:{outline.scene_location}")
    return "\n".join(lines)


def _normalize_warning(raw: dict) -> dict:
    """归一一条写前警告:severity 固定 major,类型钳制到 state|timeline。"""
    issue_type = str(raw.get("type") or "").strip().lower()
    if issue_type not in _TYPES:
        issue_type = "state"
    return {
        "severity": "major",  # 写前审核只警告不阻断,统一 major
        "type": issue_type,
        "description": str(raw.get("description") or "").strip(),
        "evidence": str(raw.get("evidence") or "").strip(),
        "conflicting_fact": str(raw.get("conflicting_fact") or "").strip(),
        "suggestion": str(raw.get("suggestion") or "").strip(),
    }


async def preflight_chapter(
    db: Session, project_id: int, chapter_number: int, outline: Outline
) -> list[dict]:
    """本章蓝图 vs 上一章契约,返回警告列表(可为空)。绝不抛异常阻塞生成。"""
    prev_contract = load_handoff_block(db, project_id, chapter_number)
    if not prev_contract:
        return []  # 上章无有效契约(第一章/老书/契约失效)→ 跳过,不报错
    prompt = PREFLIGHT_CHECK_PROMPT.format(
        chapter_number=chapter_number,
        blueprint=_blueprint_block(outline),
        prev_contract=prev_contract,
        timeline_block=timeline_block(db, project_id, chapter_number),
    )
    try:
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 审核失败降级跳过,不阻塞生成
        logger.warning("第 %d 章写前审核调用失败,跳过: %s", chapter_number, exc)
        return []
    data = parse_llm_json(raw)  # 解析失败返回 {},降级为空警告(不阻塞)
    warnings = [
        _normalize_warning(w)
        for w in (data.get("warnings") or [])
        if isinstance(w, dict)
    ]
    warnings = [w for w in warnings if w["description"]]
    if warnings:
        logger.info("第 %d 章写前审核:%d 条警告", chapter_number, len(warnings))
    return warnings
