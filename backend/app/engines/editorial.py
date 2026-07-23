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
from datetime import datetime, timezone

from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.editorial import PROOFREAD_PROMPT, REVIEW_PROMPT

# 主审四维(与前端 SCORE_LABEL / ChapterReview.scores 对应)
DIMS = ("plot", "prose", "pacing", "character")


def judge_passed(scores: dict, threshold: int) -> bool:
    """四维均 >= threshold 才算达标;缺维度(0 分)视为不达标。"""
    return all(int(scores.get(k) or 0) >= threshold for k in DIMS)


async def review_chapter(content: str, outline_block: str) -> dict:
    """主审打分:调 LLM → 解析 → 分数钳制 → 建议幻觉过滤。不碰 db。

    返回 {scores, comment, suggestions}。是否达标由调用方用 judge_passed
    按项目阈值判定(引擎函数不持有阈值)。
    """
    prompt = REVIEW_PROMPT.format(outline_block=outline_block, content=content)
    raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    data = parse_llm_json(raw)
    scores = data.get("scores") or {}
    # 分数钳制到 1-10 整数,缺维度补 0(前端显示"—")
    clean = {
        k: max(1, min(10, int(scores.get(k) or 0))) if scores.get(k) else 0
        for k in DIMS
    }
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


def build_revision_directive(review: dict) -> str:
    """把主审短评+建议拼成可注入 _revision_block 的重写意见文本(<=500 字)。"""
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
    return ";".join(parts)[:500]


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
