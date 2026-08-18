# app/api/projects/naming.py
# -*- coding: utf-8 -*-
"""AI 起名与书籍简介:书名候选、简介生成(同步/异步)。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.jobs import list_running, spawn_job
from app.llm.factory import (
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)
from app.schemas.concept import Concept

from ._common import _get_project_or_404

router = APIRouter()


# 书名 prompt:网文风格,只要名字不要解释
_TITLE_PROMPT = """\
你是网文编辑。根据下面的作品信息,起 4 个中文长篇小说书名。

【主题/灵感】{topic}
【类型】{genre}
{concept_block}
要求:
1. 网文书名风格,有记忆点,2-12 字
2. 4 个候选风格尽量拉开差异
3. 只输出书名,一行一个,不要序号、不要书名号、不要任何解释
"""


class TitleSuggestRequest(BaseModel):
    topic: str = ""
    genre: str = ""
    # 新建向导已捏出概念时传入,给起名更多上下文
    concept: Concept | None = None


class TitleSuggestResponse(BaseModel):
    titles: list[str]


@router.post("/title-suggestion", response_model=TitleSuggestResponse)
async def suggest_titles(req: TitleSuggestRequest):
    """AI 起名:用当前用户的默认模型生成 3-5 个候选书名。"""
    provider = resolve_default_provider()
    if not resolve_provider_config(provider)["api_key"]:
        raise HTTPException(
            status_code=400,
            detail="尚未配置模型,请到「模型设置」页填写 API Key。",
        )
    concept_block = ""
    if req.concept is not None and not req.concept.is_empty():
        concept_block = f"【故事概念】\n{req.concept.render()}\n"
    prompt = _TITLE_PROMPT.format(
        topic=req.topic.strip() or "(自由发挥)",
        genre=req.genre.strip() or "不限",
        concept_block=concept_block,
    )
    adapter = create_llm_adapter(provider, max_tokens=300, timeout=60)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 把失败原因直接反馈给用户
        raise HTTPException(status_code=502, detail=f"书名生成失败: {exc}") from exc

    # 逐行解析,容忍模型不守规矩的输出(序号/书名号/项目符号)
    titles: list[str] = []
    for line in raw.splitlines():
        t = re.sub(r"^\s*(?:\d+[.、)]\s*|[-*•]\s*)", "", line).strip()
        t = t.strip("《》\"'“” ")
        if t and t not in titles:
            titles.append(t)
    if not titles:
        raise HTTPException(status_code=502, detail="模型没有返回可用书名,请重试。")
    return TitleSuggestResponse(titles=titles[:5])


# 简介 prompt:网文简介风格,吸引人但不剧透结局
_SYNOPSIS_PROMPT = """\
你是网文编辑。根据下面的作品信息,写一段 150-300 字的书籍简介。

【书名】{title}
【类型】{genre}
【主题/灵感】{topic}
{core_seed}{style_block}
要求:
1. 网文简介风格:有钩子、有悬念、突出爽点与人物张力,让人想点进去看
2. 只铺垫开局与核心冲突,不要剧透结局
3. 只输出简介正文,不要标题、不要"简介:"前缀、不要任何解释
"""


class SynopsisResponse(BaseModel):
    synopsis: str


@router.post("/{project_id}/synopsis", response_model=SynopsisResponse)
async def generate_synopsis(
    project_id: int, db: Session = Depends(get_db)
) -> SynopsisResponse:
    """AI 生成书籍简介:注入主题/类型/全局倾向(有架构核心种子也带上)。"""
    project = _get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(
            status_code=400, detail="请先在「灵感」确定本书主题,再生成简介。"
        )
    core_seed = (
        f"【核心种子】{project.architecture.core_seed}\n"
        if project.architecture and project.architecture.core_seed.strip()
        else ""
    )
    prompt = _SYNOPSIS_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic,
        core_seed=core_seed,
        style_block=render_style_block(
            assemble_tendency("outline", project.global_tendency)
        ),
    )
    # 未配置 key 时工厂层抛 400(去「模型设置」页配置)
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=600, timeout=120)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 把失败原因直接反馈给用户
        raise HTTPException(status_code=502, detail=f"简介生成失败: {exc}") from exc

    synopsis = raw.strip().strip("《》\"'“” ")
    if not synopsis:
        raise HTTPException(status_code=502, detail="模型没有返回可用简介,请重试。")
    return SynopsisResponse(synopsis=synopsis)


@router.post("/{project_id}/synopsis-async")
async def generate_synopsis_async(project_id: int, db: Session = Depends(get_db)):
    """异步版简介生成:立即返回 job_id。"""
    project = _get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(
            status_code=400, detail="请先在「灵感」确定本书主题,再生成简介。"
        )
    for jid, job in list_running(f"synopsis-{project_id}"):
        if job["kind"] == f"synopsis-{project_id}":
            return {"job_id": jid}
    core_seed = (
        f"【核心种子】{project.architecture.core_seed}\n"
        if project.architecture and project.architecture.core_seed.strip()
        else ""
    )
    prompt = _SYNOPSIS_PROMPT.format(
        title=project.title,
        genre=project.genre.strip() or "不限",
        topic=project.topic,
        core_seed=core_seed,
        style_block=render_style_block(
            assemble_tendency("outline", project.global_tendency)
        ),
    )
    adapter = create_llm_adapter(resolve_default_provider(), max_tokens=600, timeout=120)

    async def work(progress):
        progress("AI 正在撰写书籍简介")
        raw = await adapter.ask(prompt)
        synopsis = raw.strip().strip("《》\"'“” ")
        if not synopsis:
            raise RuntimeError("模型没有返回可用简介,请重试。")
        return {"synopsis": synopsis}

    return {"job_id": spawn_job(f"synopsis-{project_id}", work)}
