# app/api/submission.py
# -*- coding: utf-8 -*-
"""投稿包生成接口:把项目素材压缩成知乎等平台的投稿表单字段。

POST /api/projects/{id}/submission/generate   异步生成投稿包(标题/标签/金句/简介/封面提示词)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Outline, Project
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.jobs import list_running, spawn_job
from app.llm.factory import create_llm_adapter, resolve_default_provider
from app.prompts.submission import SUBMISSION_PROMPT

logger = logging.getLogger("jarvis-write.submission")

router = APIRouter(tags=["submission"], dependencies=[Depends(get_current_user)])


def _concept_block(project: Project) -> str:
    """把结构化概念六字段渲染成提示词上下文(空字段跳过)。"""
    c = project.concept or {}
    if not isinstance(c, dict):
        return ""
    labels = {
        "logline": "一句话故事", "hook": "钩子", "twist": "反转",
        "protagonist": "主角", "conflict": "核心冲突", "setting": "世界观/设定",
    }
    lines = [f"  {labels[k]}:{c[k]}" for k in labels if c.get(k)]
    return "【故事概念】\n" + "\n".join(lines) + "\n" if lines else ""


def _outline_block(db: Session, project_id: int, limit: int = 12) -> str:
    """取前 N 章蓝图摘要做上下文(让标签/简介贴合实际剧情走向)。"""
    rows = (
        db.query(Outline.chapter_number, Outline.title, Outline.summary)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
        .limit(limit)
        .all()
    )
    if not rows:
        return ""
    lines = [f"  第{n}章《{t}》:{s}" for n, t, s in rows if s]
    return "【章节蓝图(前%d章)】\n" % len(rows) + "\n".join(lines) + "\n" if lines else ""


def _build_prompt(db: Session, project: Project) -> str:
    core_seed = (
        f"【核心种子】{project.architecture.core_seed}\n"
        if project.architecture and project.architecture.core_seed.strip()
        else ""
    )
    synopsis_block = (
        f"【全书梗概】{project.synopsis}\n"
        if project.synopsis and project.synopsis.strip()
        else ""
    )
    return SUBMISSION_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic.strip() or "(未定)",
        concept_block=_concept_block(project),
        core_seed=core_seed,
        synopsis_block=synopsis_block,
        outline_block=_outline_block(db, project.id),
    )


def _normalize(data: dict) -> dict:
    """裁剪 LLM 输出:列表去空、字数硬约束兜底,防表单字段超限。"""
    def clips(lst, n, width):
        out = []
        for x in (lst or []):
            x = str(x).strip()
            if x:
                out.append(x[:width])
            if len(out) >= n:
                break
        return out

    summaries = data.get("summaries") or {}
    if not isinstance(summaries, dict):
        summaries = {}
    return {
        "titles": clips(data.get("titles"), 3, 15),
        "channel": str(data.get("channel") or "通用").strip()[:10],
        "era": str(data.get("era") or "").strip()[:10],
        "tags": clips(data.get("tags"), 7, 12),
        "hooks": clips(data.get("hooks"), 3, 25),
        "summaries": {
            "short": str(summaries.get("short") or "").strip()[:200],
            "medium": str(summaries.get("medium") or "").strip()[:600],
            "long": str(summaries.get("long") or "").strip()[:1000],
        },
        "cover_prompts": clips(data.get("cover_prompts"), 2, 500),
    }


async def _generate_impl(db: Session, project: Project) -> dict:
    prompt = _build_prompt(db, project)
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=2500, timeout=180)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"投稿包生成失败: {exc}") from exc
    try:
        data = parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="投稿包解析失败,请重试") from exc
    return _normalize(data)


@router.post("/api/projects/{project_id}/submission/generate")
async def generate_submission(project_id: int, db: Session = Depends(get_db)):
    """异步生成投稿包:立即返回 job_id,前端轮询取结果。"""
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再生成投稿包。")
    for jid, job in list_running(f"submission-{project_id}"):
        if job["kind"] == f"submission-{project_id}":
            return {"job_id": jid}

    prompt = _build_prompt(db, project)
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=2500, timeout=180)

    async def work(progress):
        progress("AI 正在生成投稿包(标题/标签/金句/简介/封面)")
        raw = await adapter.ask(prompt)
        data = parse_llm_json(raw)
        return _normalize(data)

    return {"job_id": spawn_job(f"submission-{project_id}", work)}
