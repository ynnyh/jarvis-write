# app/api/media.py
# -*- coding: utf-8 -*-
"""周边创作接口:封面图提示词 / 主题曲提示词。

我们只产"提示词",不接绘图/音乐模型——用户拿去即梦/MJ/Suno 自己生成。
两条接口都异步(立即返回 job_id,前端轮询),复用 submission 的上下文拼装。

POST /api/projects/{id}/cover/generate    3 套封面画面提示词(中文/英文/负面词)
POST /api/projects/{id}/anthem/generate   Suno 主题曲(英文风格标签 + 中文歌词)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.api.submission import _build_context_blocks
from app.auth import get_current_user
from app.db.models import Project
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.jobs import list_running, spawn_job
from app.llm.factory import create_llm_adapter, resolve_default_provider
from app.prompts.media import ANTHEM_PROMPT, COVER_PROMPT

logger = logging.getLogger("jarvis-write.media")

router = APIRouter(tags=["media"], dependencies=[Depends(get_current_user)])


def _clip(s: object, width: int) -> str:
    return str(s or "").strip()[:width]


# =============== 封面提示词 ===============
def _normalize_covers(data: dict) -> dict:
    """裁剪封面输出:最多 3 套,字段各自限长,防脏数据/超长。"""
    out = []
    for c in (data.get("covers") or []):
        if not isinstance(c, dict):
            continue
        item = {
            "style": _clip(c.get("style"), 60),
            "prompt_cn": _clip(c.get("prompt_cn"), 1000),
            "prompt_en": _clip(c.get("prompt_en"), 1000),
            "negative": _clip(c.get("negative"), 500),
        }
        # 至少要有中文或英文提示词才算一条有效方案
        if item["prompt_cn"] or item["prompt_en"]:
            out.append(item)
        if len(out) >= 3:
            break
    return {"covers": out}


@router.post("/api/projects/{project_id}/cover/generate")
async def generate_cover(project_id: int, db: Session = Depends(get_db)):
    """异步生成封面提示词:立即返回 job_id,前端轮询取结果。"""
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再生成封面提示词。")
    for jid, job in list_running(f"cover-{project_id}"):
        if job["kind"] == f"cover-{project_id}":
            return {"job_id": jid}

    prompt = COVER_PROMPT.format(**_build_context_blocks(db, project))
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=2500, timeout=180)

    async def work(progress):
        progress("AI 正在设计封面提示词(3 套风格)")
        raw = await adapter.ask(prompt)
        return _normalize_covers(parse_llm_json(raw))

    return {"job_id": spawn_job(f"cover-{project_id}", work)}


# =============== 主题曲提示词(Suno) ===============
def _normalize_anthem(data: dict) -> dict:
    """裁剪主题曲输出:各字段限长。"""
    return {
        "song_title": _clip(data.get("song_title"), 40),
        "style_tags": _clip(data.get("style_tags"), 400),
        "lyrics": _clip(data.get("lyrics"), 3000),
        "vibe": _clip(data.get("vibe"), 300),
    }


@router.post("/api/projects/{project_id}/anthem/generate")
async def generate_anthem(project_id: int, db: Session = Depends(get_db)):
    """异步生成主题曲提示词(Suno):立即返回 job_id,前端轮询取结果。"""
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再生成主题曲。")
    for jid, job in list_running(f"anthem-{project_id}"):
        if job["kind"] == f"anthem-{project_id}":
            return {"job_id": jid}

    prompt = ANTHEM_PROMPT.format(**_build_context_blocks(db, project))
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=2500, timeout=180)

    async def work(progress):
        progress("AI 正在创作主题曲(Suno 风格标签 + 中文歌词)")
        raw = await adapter.ask(prompt)
        return _normalize_anthem(parse_llm_json(raw))

    return {"job_id": spawn_job(f"anthem-{project_id}", work)}
