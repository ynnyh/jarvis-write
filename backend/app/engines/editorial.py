# app/engines/editorial.py
# -*- coding: utf-8 -*-
"""编辑部审校引擎:主审打分 / 校对硬伤 / 精确替换 / 达标判定。

从 api/editorial.py 的闭包里抽出来的纯逻辑(不碰 db),供 API 层与章节生成
流水线的「审校把关」复用。达标与否由后端按项目阈值硬判(judge_passed),
不靠模型自报——阈值是用户可调的硬约束,模型只负责打分与给建议。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import GATE_REPAIR_PROMPT
from app.prompts.editorial import PROOFREAD_PROMPT, REVIEW_PROMPT

logger = logging.getLogger("jarvis-write.editorial")

# 主审四维(与前端 SCORE_LABEL / ChapterReview.scores 对应)
DIMS = ("plot", "prose", "pacing", "character")
# 第五维「连续性」(docs/08 §5.4.4):不来自主审 LLM,由一致性门禁结果折算
# (checker.continuity_score)后写入 scores。仅在 scores 里存在时参与达标判定——
# 编辑部手动主审(纯四维)与四维旧快照维持原判定,向后兼容。
CONTINUITY_DIM = "continuity"


def _clamp_score(v) -> int:
    """把 LLM 给的单维分数宽容转成 0(缺失/非法)或 1-10 的整数。

    LLM 可能返回 8 / "8" / 8.5 / "优秀" / None / 越界值 —— 非数字或缺失记 0
    (前端显示"—");有效数字钳到 1-10(过小/负数→1,过大→10);明确 0 分同样记 0。
    绝不让 int()/float() 的 ValueError 穿透、带崩整个审校。
    """
    try:
        n = int(float(str(v).strip()))
    except (ValueError, TypeError, OverflowError):
        return 0
    return max(1, min(10, n)) if n != 0 else 0


def judge_passed(scores: dict, threshold: int) -> bool:
    """四维均 >= threshold 才算达标;缺维度(0 分)视为不达标。

    scores 含 continuity 维度(生成流水线的门禁折算分)时,该维同样必须 >= threshold。
    """
    base = all(int(scores.get(k) or 0) >= threshold for k in DIMS)
    if CONTINUITY_DIM in scores:
        return base and int(scores.get(CONTINUITY_DIM) or 0) >= threshold
    return base


async def review_chapter(content: str, outline_block: str) -> dict:
    """主审打分:调 LLM → 解析 → 分数钳制 → 建议幻觉过滤。不碰 db。

    返回 {scores, comment, suggestions}。是否达标由调用方用 judge_passed
    按项目阈值判定(引擎函数不持有阈值)。
    """
    prompt = REVIEW_PROMPT.format(outline_block=outline_block, content=content)
    raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    data = parse_llm_json(raw)
    scores = data.get("scores") or {}
    # 分数钳制到 1-10 整数,缺维度/非法值补 0(前端显示"—")
    clean = {k: _clamp_score(scores.get(k)) for k in DIMS}
    # 建议:结构化 {evidence, issue, fix};evidence 必须在正文里逐字存在(防举证幻觉),
    # 找不到的置空但保留建议本身。兼容模型退化输出纯字符串的情况。达标时可为空数组。
    suggestions = []
    for s in (data.get("suggestions") or [])[:3]:
        if isinstance(s, str):
            suggestions.append({"evidence": "", "issue": s.strip(), "fix": ""})
            continue
        if not isinstance(s, dict):
            continue
        evidence = str(s.get("evidence") or "").strip()
        if evidence and evidence not in content:
            evidence = ""
        suggestions.append({
            "evidence": evidence,
            "issue": str(s.get("issue") or "").strip(),
            "fix": str(s.get("fix") or "").strip(),
        })
    return {
        "scores": clean,
        # 每维一句话评分依据(锚点化评分的可解释性;缺失/超长 defensively 收敛)
        "score_reasons": {
            k: str((data.get("score_reasons") or {}).get(k) or "").strip()[:120]
            for k in DIMS
        },
        "comment": str(data.get("comment") or "").strip(),
        "suggestions": [s for s in suggestions if s["issue"] or s["fix"]],
    }


async def proofread_chapter(content: str) -> dict:
    """校对硬伤:调 LLM → 解析 → 幻觉过滤。返回 {issues}。不碰 db。"""
    prompt = PROOFREAD_PROMPT.format(content=content)
    raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    data = parse_llm_json(raw)
    issues = []
    for it in (data.get("issues") or [])[:20]:
        if not isinstance(it, dict):
            continue
        original = str(it.get("original") or "")
        suggestion = str(it.get("suggestion") or "")
        # 只保留能在正文中定位到的问题,幻觉片段直接丢弃
        if not original or not suggestion or original == suggestion:
            continue
        if original not in content:
            continue
        issues.append({
            "type": str(it.get("type") or "typo"),
            "original": original,
            "suggestion": suggestion,
            "reason": str(it.get("reason") or "").strip(),
        })
    return {"issues": issues}


def apply_proofread_fixes(
    content: str, issues: list[dict]
) -> tuple[str, list[dict], list[dict]]:
    """把校对问题逐条精确替换首次出现。返回 (new_content, applied, failed)。

    纯字符串操作,不碰 db;留快照/落库由调用方决定。
    """
    applied, failed = [], []
    for it in issues:
        original = str(it.get("original") or "")
        suggestion = str(it.get("suggestion") or "")
        if not original or original == suggestion:
            failed.append({"original": original, "reason": "无效修复项"})
            continue
        at = content.find(original)
        if at < 0:
            failed.append({"original": original, "reason": "正文中已找不到该片段"})
            continue
        content = content[:at] + suggestion + content[at + len(original):]
        applied.append({"original": original, "suggestion": suggestion})
    return content, applied, failed


# ---------- 门禁问题定点修复(分级回炉的 patch 路径,docs/08 §5.4) ----------

async def repair_chapter(chapter_number: int, content: str, issues: list[dict]) -> list[dict]:
    """门禁 blocker 定点修复:调 LLM 出「逐字锚 → 最小改动」替换对,返回 fixes(可空)。

    只产出修复方案,不改正文——应用与校验在 apply_gate_fixes。调用失败/解析失败
    返回空列表(定点修复是省重写的优化路径,失败由调用方回退重写,不拖垮生成)。
    """
    lines = []
    for idx, i in enumerate(issues):
        lines.append(
            f"{idx}. [{i.get('type') or 'state'}] {i.get('description')}\n"
            f"   证据:{i.get('evidence')}\n"
            f"   被违反事实:{i.get('conflicting_fact') or '(未给出)'}\n"
            f"   修正建议:{i.get('suggestion') or '(未给出)'}"
        )
    prompt = GATE_REPAIR_PROMPT.format(
        chapter_number=chapter_number,
        chapter_text=content[:12000],  # 对齐 checker 的正文截断口径
        issues_block="\n".join(lines) or "(无)",
    )
    try:
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 修复失败回退重写,不阻塞生成
        logger.warning("第 %d 章门禁定点修复调用失败(将回退重写): %s", chapter_number, exc)
        return []
    data = parse_llm_json(raw)
    fixes = []
    for f in (data.get("fixes") or [])[:20]:
        if not isinstance(f, dict):
            continue
        original = str(f.get("original") or "")
        replacement = str(f.get("replacement") or "")
        if not original or original == replacement:
            continue
        fixes.append({
            "issue_index": f.get("issue_index"),
            "original": original,
            "replacement": replacement,
        })
    return fixes


def apply_gate_fixes(
    content: str, fixes: list[dict]
) -> tuple[str, list[dict], list[dict]]:
    """把定点修复逐条应用:锚必须逐字且全篇唯一。返回 (new_content, applied, failed)。

    与 apply_proofread_fixes 的差别在唯一性校验:修复改的是事实点,锚不唯一时
    首处替换可能改错位置(校对改错字无此顾虑)——宁可 failed 也不误伤;没修干净
    的问题门禁复查会重新报,调用方据此回退重写。纯字符串操作,顺序应用,
    前一条替换使后一条锚失效时后者自然记 failed。
    """
    applied, failed = [], []
    for it in fixes:
        original = str(it.get("original") or "")
        replacement = str(it.get("replacement") or "")
        if not original or original == replacement:
            failed.append({"original": original, "reason": "无效修复项"})
            continue
        hits = content.count(original)
        if hits == 0:
            failed.append({"original": original, "reason": "正文中找不到该片段"})
            continue
        if hits > 1:
            failed.append({"original": original, "reason": "片段不唯一,拒绝误伤"})
            continue
        at = content.find(original)
        content = content[:at] + replacement + content[at + len(original):]
        applied.append({"original": original, "replacement": replacement})
    return content, applied, failed


def build_revision_directive(review: dict) -> str:
    """把主审短评+建议拼成可注入 _revision_block 的重写意见文本(<=800 字)。

    上限从 500 放宽到 800:回炉指令里常含多条建议+门禁 blocker 的「证据+改法」,
    500 字会把排在后面的 blocker 修法截掉——writer 没看到要改什么,重写自然不收敛。
    """
    parts = []
    comment = (review.get("comment") or "").strip()
    if comment:
        parts.append(f"主编总评:{comment}")
    for s in review.get("suggestions") or []:
        seg = ""
        if s.get("evidence"):
            seg += f"\"{s['evidence']}\"这里:"
        seg += s.get("issue") or ""
        if s.get("fix"):
            seg += f",改法:{s['fix']}"
        if seg:
            parts.append(seg)
    return ";".join(parts)[:800]


# ---------- 审校快照(编辑部回显用) ----------

def content_hash(text: str) -> str:
    """正文 SHA-256 指纹(取前 16 位),用于判断审校快照是否对应当前正文。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def store_review_snapshot(chapter, review: dict, source: str, content: str) -> None:
    """把主审结果写进章节快照字段(不 commit,由调用方随事务提交)。

    source: "generation"(生成时审校)/ "manual"(编辑部手动主审)。
    content: 本次审校所对应的正文——回显时指纹与当前正文一致才显示,
    正文被编辑/润色/重写/回滚后自动失效,不会给用户看过期的评分。
    """
    snapshot = dict(review)
    snapshot["source"] = source
    snapshot["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["content_hash"] = content_hash(content)
    chapter.review_snapshot = json.dumps(snapshot, ensure_ascii=False)


def load_review_snapshot(chapter) -> dict | None:
    """读取章节审校快照;无快照、损坏或正文已改动(指纹不符)时返回 None。"""
    raw = getattr(chapter, "review_snapshot", "") or ""
    if not raw.strip():
        return None
    try:
        snapshot = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if snapshot.get("content_hash") != content_hash(chapter.final_content or ""):
        return None
    return snapshot


def store_proofread_snapshot(
    chapter, issues: list[dict], source: str, content: str, fixed: int | None = None
) -> None:
    """把校对结果写进章节快照字段(不 commit,由调用方随事务提交)。

    issues:问题清单([{type, original, suggestion, reason}, ...])。
    source:"generation"(生成时校对,已自动修复,只读回显)/ "manual"(手动校对,待修)。
    fixed:已修复数;缺省取 issues 长度(生成时即自动修复数),手动待修时传 0。
    content:本次校对所对应的正文——回显时指纹与当前正文一致才显示,正文被
    编辑/润色/重写/回滚后自动失效,不会给用户看过期的校对清单。
    """
    snapshot = {
        "issues": issues,
        "fixed": len(issues) if fixed is None else fixed,
        "source": source,
        "proofread_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash(content),
    }
    chapter.proofread_snapshot = json.dumps(snapshot, ensure_ascii=False)


def load_proofread_snapshot(chapter) -> dict | None:
    """读取章节校对快照;无快照、损坏或正文已改动(指纹不符)时返回 None。"""
    raw = getattr(chapter, "proofread_snapshot", "") or ""
    if not raw.strip():
        return None
    try:
        snapshot = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if snapshot.get("content_hash") != content_hash(chapter.final_content or ""):
        return None
    return snapshot
