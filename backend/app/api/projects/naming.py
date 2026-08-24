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
from app.llm.router import Task, get_adapter_for
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


def _title_prompt(req: TitleSuggestRequest) -> str:
    """入参 → 起名提示词(同步版与异步版共用,避免两条口径分叉)。"""
    concept_block = ""
    if req.concept is not None and not req.concept.is_empty():
        concept_block = f"【故事概念】\n{req.concept.render()}\n"
    return _TITLE_PROMPT.format(
        topic=req.topic.strip() or "(自由发挥)",
        genre=req.genre.strip() or "不限",
        concept_block=concept_block,
    )


def _title_adapter():
    """起名用的适配器。

    走 `get_adapter_for` 而不是按协议名造——按协议名只会取到该协议里**创建最早**
    的那套配置,用户在设置页标「默认」的那套会被丢掉(线上就是这么打到一个缺 /v1
    的中转站,吃了 Cloudflare 挑战页的 HTTP 403,换成官方 DeepSeek 也没用)。

    timeout 给到 120s:`ask()` 内部遇到空正文/截断会放大预算重试,一次起名可能
    是两三轮上游调用,60s 根本不够——异步化之前前端也是 60s 掐断,两边同时到点,
    浏览器只吐一句 `Failed to fetch`。
    """
    return get_adapter_for(Task.TITLE, max_tokens=300, timeout=120)


def _parse_titles(raw: str) -> list[str]:
    """逐行解析,容忍模型不守规矩的输出(序号/书名号/项目符号);最多 5 个。"""
    titles: list[str] = []
    for line in raw.splitlines():
        t = re.sub(r"^\s*(?:\d+[.、)]\s*|[-*•]\s*)", "", line).strip()
        t = t.strip("《》\"'“” ")
        if t and t not in titles:
            titles.append(t)
    return titles[:5]


@router.post("/title-suggestion", response_model=TitleSuggestResponse)
async def suggest_titles(req: TitleSuggestRequest):
    """AI 起名(同步版,留给旧客户端):用当前用户的默认模型生成 3-5 个候选书名。

    新前端走下面的 `-async` 版——同步版要把 HTTP 连接挂住整轮 LLM 调用,是这条线
    唯一还这么干的接口。未配置 key 时由工厂层抛 400(与简介生成同一条路径),
    这里不再自己预检一遍:那份预检走的是「按协议名取最早一套配置」的旧路径,
    和真正发请求用的配置会分叉。
    """
    prompt = _title_prompt(req)
    adapter = _title_adapter()
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 把失败原因直接反馈给用户
        raise HTTPException(status_code=502, detail=f"书名生成失败: {exc}") from exc

    titles = _parse_titles(raw)
    if not titles:
        raise HTTPException(status_code=502, detail="模型没有返回可用书名,请重试。")
    return TitleSuggestResponse(titles=titles)


@router.post("/title-suggestion-async")
async def suggest_titles_async(req: TitleSuggestRequest):
    """异步版 AI 起名:立即返回 job_id,前端轮询取候选。

    为什么起名也得走后台任务:一轮起名是分钟级的 LLM 调用(慢思考模型更久,
    还可能因空正文重试两三轮)。同步版把连接一直挂住,家用网络/NAT/代理的空闲
    超时一掐,浏览器只抛一句 `TypeError: Failed to fetch`——用户看不出是超时、
    是断网还是服务挂了,后端日志里连请求都没有。简介早先因为同样的原因加了
    `-async`,起名是最后一条同步长请求。

    不做 `list_running` 去重:起名的入参(主题/类型/概念/命名偏好)每次都可能不同,
    复用在跑的任务会把上一次入参的候选返给用户;这活儿本身也便宜,重复提交无所谓。
    """
    prompt = _title_prompt(req)
    # 未配置 key 时工厂层抛 400——放在 spawn_job 之前,让它同步冒出去
    adapter = _title_adapter()

    async def work(progress):
        progress("AI 正在起书名")
        titles = _parse_titles(await adapter.ask(prompt))
        if not titles:
            raise RuntimeError("模型没有返回可用书名,请重试。")
        return {"titles": titles}

    return {"job_id": spawn_job("title-suggestion", work)}


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
    adapter = get_adapter_for(Task.SYNOPSIS, max_tokens=600, timeout=120)
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
    adapter = get_adapter_for(Task.SYNOPSIS, max_tokens=600, timeout=120)

    async def work(progress):
        progress("AI 正在撰写书籍简介")
        raw = await adapter.ask(prompt)
        synopsis = raw.strip().strip("《》\"'“” ")
        if not synopsis:
            raise RuntimeError("模型没有返回可用简介,请重试。")
        return {"synopsis": synopsis}

    return {"job_id": spawn_job(f"synopsis-{project_id}", work)}
