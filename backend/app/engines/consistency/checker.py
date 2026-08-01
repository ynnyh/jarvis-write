# app/engines/consistency/checker.py
# -*- coding: utf-8 -*-
"""一致性检查(写后一致性门禁的对照引擎,docs/08 §5.4)。

对照源(三路,缺一降级不跳过):
  1. 故事圣经硬约束(active_facts)——圣经为空不再直接跳过,走"仅对照上章"降级路径;
  2. 上一章章末交接契约(chapter_states,带正文指纹校验,失效则不注入);
  3. 上一章结尾原文(结尾 900 字,对齐生成时 recent_tail 的取法)。
三路全空(第一章且圣经为空)才返回空列表——没有任何可对照的事实源。

输出格式:每条问题含 问题点(description)/证据段落(evidence,正文逐字引用,
引不到的幻觉举证清空)/被违反事实(conflicting_fact)/修正建议(suggestion)/
severity(blocker|major|minor)/type(state|knowledge|timeline|worldrule)。

检查结果落 chapter_issues 表(persist_issues,purge 旧 open 幂等重建),
供门禁判定(quarantined)与 P1 审核面板使用。检查失败(LLM 异常/解析失败)
返回空列表并告警,不阻塞流程。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterIssue, ChapterState
from app.engines.consistency.bible import BibleService
from app.engines.consistency.extractor import parse_llm_json
from app.engines.editorial import content_hash
from app.engines.pipeline.handoff import _fresh_contract, format_contract_block
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import CONSISTENCY_CHECK_PROMPT

logger = logging.getLogger("jarvis-write.checker")

_PREV_TAIL_CHARS = 900  # 上一章结尾原文截断长度(对齐 chapter.py 的 _RECENT_TAIL_CHARS)

_SEVERITIES = {"blocker", "major", "minor"}
_TYPES = {"state", "knowledge", "timeline", "worldrule"}


def _prev_chapter_context(db: Session, project_id: int, chapter_number: int) -> tuple[str, str]:
    """取上一章的对照材料:(契约文本块, 结尾原文块);无上一章/无正文 → ("", "")。"""
    if chapter_number <= 1:
        return "", ""
    prev = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number - 1)
        .first()
    )
    if prev is None or not prev.final_content:
        return "", ""
    tail = f"(第{prev.chapter_number}章结尾)…{prev.final_content[-_PREV_TAIL_CHARS:]}"
    row = db.query(ChapterState).filter(ChapterState.chapter_id == prev.id).first()
    contract = _fresh_contract(row, prev)  # 带指纹校验,失效契约不用于对照
    block = format_contract_block(contract, prev.chapter_number) if contract else ""
    return block, tail


def _normalize_issue(raw: dict, chapter_text: str) -> dict:
    """归一一条 LLM 问题:severity/type 钳制到约定枚举,幻觉举证清空。

    severity 兼容旧 prompt 的 critical 措辞(→ blocker);未知值降为 minor,
    宁可低估也不让脏数据把章节误判进 quarantined。
    """
    severity = str(raw.get("severity") or "").strip().lower()
    if severity == "critical":
        severity = "blocker"
    if severity not in _SEVERITIES:
        severity = "minor"
    issue_type = str(raw.get("type") or "").strip().lower()
    if issue_type not in _TYPES:
        issue_type = "state"
    # 证据必须是本章正文逐字引用(对齐 review_chapter 的防幻觉举证):引不到置空
    evidence = str(raw.get("evidence") or "").strip()
    if evidence and evidence not in chapter_text:
        evidence = ""
    return {
        "severity": severity,
        "type": issue_type,
        "description": str(raw.get("description") or "").strip(),
        "evidence": evidence,
        "conflicting_fact": str(raw.get("conflicting_fact") or "").strip(),
        "suggestion": str(raw.get("suggestion") or "").strip(),
    }


async def check_chapter(
    db: Session,
    project_id: int,
    chapter_number: int,
    chapter_text: str,
    rolling_summary: str = "",
) -> list[dict]:
    """返回问题列表 [{severity,type,description,evidence,conflicting_fact,suggestion}]。

    检查失败(LLM 异常/解析失败)返回空列表并告警,不阻塞流程。
    """
    bible = BibleService(db, project_id)
    active_facts = bible.hard_constraints_block(chapter_number)
    prev_contract, prev_tail = _prev_chapter_context(db, project_id, chapter_number)
    has_bible = not active_facts.startswith("(暂无")
    if not has_bible and not prev_contract and not prev_tail:
        return []  # 第一章且圣经为空:没有任何可对照的事实源
    if not has_bible:
        # 圣经为空 → 降级为"仅对照上章"(docs/08 §5.4.1),不再直接跳过
        active_facts = "(故事圣经暂无有效事实,本次仅对照上一章契约与结尾原文)"

    prompt = CONSISTENCY_CHECK_PROMPT.format(
        active_facts=active_facts,
        prev_contract=prev_contract or "(无上一章契约——未提取或正文已改动失效)",
        prev_tail=prev_tail or "(无上一章结尾原文,本章可能是第一章)",
        rolling_summary=rolling_summary or "(无)",
        chapter_number=chapter_number,
        chapter_text=chapter_text[:12000],
    )
    try:
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.error("一致性检查调用失败: %s", exc)
        return []

    data = parse_llm_json(raw)
    issues = [
        _normalize_issue(i, chapter_text)
        for i in (data.get("issues") or [])
        if isinstance(i, dict)
    ]
    # 没有问题点描述的条目没有落库/展示价值
    issues = [i for i in issues if i["description"]]
    if issues:
        logger.warning(
            "第 %d 章发现 %d 个一致性问题(blocker=%d)",
            chapter_number, len(issues), len(blockers_of(issues)),
        )
    return issues


# ---------- 门禁判定辅助(纯函数,供 pipeline 与 API 复用) ----------

def blockers_of(issues: list[dict]) -> list[dict]:
    """筛出 blocker 级问题(门禁阻断项)。"""
    return [i for i in issues if i.get("severity") == "blocker"]


def continuity_score(issues: list[dict]) -> int:
    """门禁结果折算审校第五维「连续性」分数(docs/08 §5.4.4)。

    简单映射:blocker → 4(必不达常规阈值,触发回炉);major → 6;
    仅 minor → 8;干净 → 9。
    """
    severities = {i.get("severity") for i in issues}
    if "blocker" in severities:
        return 4
    if "major" in severities:
        return 6
    if "minor" in severities:
        return 8
    return 9


def persist_issues(
    db: Session, chapter: Chapter, issues: list[dict], *, source: str, text: str
) -> None:
    """本章 issues 幂等落库(不 commit,由调用方随事务提交)。

    清旧账:purge 本章同来源的旧 open 记录,按当前结果重建 open 集(其他来源
    ——如 P1 的 preflight/diag——的 open 不动);正文指纹已变化的 ignored 记录
    一并清除(不再生效,同一矛盾下次会重新报警),指纹未变的 ignored 保留
    (用户已确认忽略,不重报)。
    """
    current_hash = content_hash(text)
    old = (
        db.query(ChapterIssue)
        .filter(ChapterIssue.chapter_id == chapter.id, ChapterIssue.source == source)
        .all()
    )
    for row in old:
        if row.status == "open" or row.content_hash != current_hash:
            db.delete(row)
    if old:
        db.flush()
    for i in issues:
        db.add(ChapterIssue(
            chapter_id=chapter.id,
            source=source,
            severity=i.get("severity") or "minor",
            issue_type=i.get("type") or "state",
            description=i.get("description") or "",
            evidence=i.get("evidence") or "",
            suggestion=i.get("suggestion") or "",
            status="open",
            content_hash=current_hash,
        ))
