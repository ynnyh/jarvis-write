# app/api/editorial.py
# -*- coding: utf-8 -*-
"""编辑部接口:主编评分 / 校对 / 审核报告 / 优化动作目录。

GET  /api/editorial/actions                          预设优化动作(正文/大纲两级,配置文件驱动)
POST /api/projects/{id}/chapters/{n}/review-async    主编评分(四维+短评+3条建议)
POST /api/projects/{id}/chapters/{n}/proofread-async 校对(错别字/语病/标点/重复,问题清单)
POST /api/projects/{id}/chapters/{n}/proofread-apply 应用勾选的校对修复(逐条精确替换)
GET  /api/projects/{id}/audit-report                 审核报告(聚合失配章/伏笔/退场人物,零 LLM)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Foreshadowing, Outline
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.jobs import list_running, spawn_job
from app.llm.router import Task, get_adapter_for
from app.prompts.editorial import PROOFREAD_PROMPT, REVIEW_PROMPT

router = APIRouter(tags=["editorial"], dependencies=[Depends(get_current_user)])

_ACTIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "editor_actions.json"


@lru_cache
def _actions() -> dict:
    with open(_ACTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/editorial/actions")
async def editorial_actions() -> dict:
    """预设优化动作目录(前端渲染 chips;prose=正文级,outline=大纲级)。"""
    return _actions()


def _chapter_with_content(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚无定稿正文")
    return ch


# ---------- 主编评分 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/review-async")
async def review_async(project_id: int, n: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"review-{project_id}-"):
        if job["kind"] == f"review-{project_id}-{n}":
            return {"job_id": jid}
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    outline_block = (
        f"标题:{outline.title}\n目的:{outline.chapter_purpose}\n概要:{outline.summary}"
        if outline else "(无蓝图)"
    )
    prompt = REVIEW_PROMPT.format(outline_block=outline_block, content=ch.final_content)
    content = ch.final_content

    async def work(progress):
        progress(f"主编正在审读第 {n} 章")
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
        data = parse_llm_json(raw)
        scores = data.get("scores") or {}
        # 分数钳制到 1-10 整数,缺维度补 0(前端显示"—")
        clean = {
            k: max(1, min(10, int(scores.get(k) or 0))) if scores.get(k) else 0
            for k in ("plot", "prose", "pacing", "character")
        }
        # 建议:结构化 {evidence, issue, fix};evidence 必须在正文里逐字存在(防举证幻觉),
        # 找不到的置空但保留建议本身。兼容模型退化输出纯字符串的情况。
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
            "chapter_number": n,
            "scores": clean,
            "comment": str(data.get("comment") or "").strip(),
            "suggestions": [s for s in suggestions if s["issue"] or s["fix"]],
        }

    return {"job_id": spawn_job(f"review-{project_id}-{n}", work)}


# ---------- 校对 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/proofread-async")
async def proofread_async(project_id: int, n: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"proofread-{project_id}-"):
        if job["kind"] == f"proofread-{project_id}-{n}":
            return {"job_id": jid}
    content = ch.final_content
    prompt = PROOFREAD_PROMPT.format(content=content)

    async def work(progress):
        progress(f"校对正在逐句检查第 {n} 章")
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
        data = parse_llm_json(raw)
        issues = []
        for it in (data.get("issues") or [])[:20]:
            if not isinstance(it, dict):
                continue
            original = str(it.get("original") or "")
            suggestion = str(it.get("suggestion") or "")
            # 只保留能在正文中唯一/首次定位到的问题,幻觉片段直接丢弃
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
        return {"chapter_number": n, "issues": issues}

    return {"job_id": spawn_job(f"proofread-{project_id}-{n}", work)}


class ProofreadApplyRequest(BaseModel):
    fixes: list[dict] = Field(min_length=1, max_length=20, description="[{original, suggestion}]")


@router.post("/api/projects/{project_id}/chapters/{n}/proofread-apply")
async def proofread_apply(
    project_id: int, n: int, req: ProofreadApplyRequest, db: Session = Depends(get_db)
):
    """应用勾选的校对修复:逐条精确替换首次出现;改前留版本快照。

    返回 applied/failed 清单;正文有实质变化时建议前端随后调 re-extract-async。
    """
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    content = ch.final_content
    applied, failed = [], []
    for fix in req.fixes:
        original = str(fix.get("original") or "")
        suggestion = str(fix.get("suggestion") or "")
        if not original or original == suggestion:
            failed.append({"original": original, "reason": "无效修复项"})
            continue
        at = content.find(original)
        if at < 0:
            failed.append({"original": original, "reason": "正文中已找不到该片段"})
            continue
        content = content[:at] + suggestion + content[at + len(original):]
        applied.append({"original": original, "suggestion": suggestion})
    if applied:
        snapshot_chapter(db, ch, source="edited")
        ch.final_content = content
        ch.word_count = len(content)
        db.commit()
    return {
        "applied": applied,
        "failed": failed,
        "word_count": ch.word_count,
        "final_content": ch.final_content,
    }


# ---------- 审核报告(零 LLM,聚合现有数据) ----------

@router.get("/api/projects/{project_id}/audit-report")
async def audit_report(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    written = {c.chapter_number for c in chapters}
    max_written = max(written) if written else 0
    stale = [c.chapter_number for c in chapters if c.is_stale]

    fores = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .order_by(Foreshadowing.chapter_planted)
        .all()
    )
    # 逾期:预期回收章已写过但状态仍未回收
    overdue = [
        {
            "description": f.description,
            "planted": f.chapter_planted,
            "expected": f.expected_payoff_chapter,
            "status": f.status,
        }
        for f in fores
        if f.status in ("planted", "reinforced")
        and f.expected_payoff_chapter is not None
        and f.expected_payoff_chapter <= max_written
    ]
    open_count = sum(1 for f in fores if f.status in ("planted", "reinforced"))
    resolved_count = sum(1 for f in fores if f.status == "paid_off")

    # 大纲已生成但长期没写的章(跳章检查:前面留洞)
    outline_nums = [
        o.chapter_number
        for o in db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    ]
    holes = [
        num for num in outline_nums if num < max_written and num not in written
    ]

    return {
        "written_chapters": len(chapters),
        "target_chapters": project.target_chapters,
        "stale_chapters": stale,
        "holes": holes,
        "foreshadow": {
            "total": len(fores),
            "open": open_count,
            "resolved": resolved_count,
            "overdue": overdue,
        },
    }
