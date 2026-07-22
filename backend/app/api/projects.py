# app/api/projects.py
# -*- coding: utf-8 -*-
"""项目管理 + 生成流水线接口。

阶段 1 核心链路:
  POST /api/projects                          建项目(带全局倾向)
  POST /api/projects/{id}/architecture        雪花四步生成顶层架构
  POST /api/projects/{id}/architecture-async  同上,异步任务(前端轮询进度)
  POST /api/projects/{id}/blueprint           分块生成章节蓝图并落库
  POST /api/projects/{id}/blueprint-async     同上,异步任务(前端轮询进度)
  GET  /api/projects/{id}/outlines            查看章节目录
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import delete_project_cascade
from app.auth import assert_project_owner, current_user_id, get_current_user
from app.db.models import Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.architecture import generate_architecture, save_architecture
from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.jobs import create_job, fail_job, finish_job, list_running, spawn_job, update_stage
from app.llm.factory import (
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)
from app.schemas.concept import Concept
from app.schemas.project import (
    ArchitectureOut,
    GenerateArchitectureRequest,
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    OutlineOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)


def _get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(project)
    return project


@router.post("", response_model=ProjectOut)
async def create_project(req: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    concept_dict = None
    topic = req.topic
    if req.concept is not None and not req.concept.is_empty():
        concept_dict = req.concept.model_dump()
        if not topic.strip() and req.concept.logline.strip():
            topic = req.concept.logline.strip()
    project = Project(
        user_id=current_user_id.get(),
        title=req.title,
        topic=topic,
        concept=concept_dict,
        setup_state=req.setup_state,
        genre=req.genre,
        target_chapters=req.target_chapters,
        target_words_per_chapter=req.target_words_per_chapter,
        global_tendency=req.global_tendency,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    uid = current_user_id.get()
    return list(
        db.query(Project)
        .filter(Project.user_id == uid)
        .order_by(Project.id.desc())
    )


# ---------- AI 起名 ----------

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


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
    return _get_project_or_404(db, project_id)


# ---------- 书籍简介 ----------

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


class ProjectPatch(BaseModel):
    title: str | None = None
    topic: str | None = None
    genre: str | None = None
    target_chapters: int | None = None
    target_words_per_chapter: int | None = None
    global_tendency: dict | None = None
    concept: Concept | None = None
    synopsis: str | None = None
    # 起步流进度:传 "" 表示起步完成(落库为 NULL)
    setup_state: str | None = None
    # 灵感对话记录(整段覆盖式保存)
    chat_log: list | None = None


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: int, req: ProjectPatch, db: Session = Depends(get_db)
) -> Project:
    """修改项目信息(重命名标题、灵感区确定主题、调整全局倾向等)。

    定概念:传 concept 时落库结构化概念,并把 topic 同步为 logline
    (下游 title/简介仍读 topic,保持单一真相源)。显式传 topic 优先于同步。
    """
    project = _get_project_or_404(db, project_id)
    updates = req.model_dump(exclude_none=True)
    if "title" in updates:
        title = updates["title"].strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        if len(title) > 100:
            raise HTTPException(status_code=400, detail="标题过长,最多 100 字")
        updates["title"] = title
    if "concept" in updates:
        # concept 存为纯 dict(JSON 列);topic 跟随 logline,除非本次显式改了 topic
        concept: Concept = req.concept  # 已通过 pydantic 校验
        updates["concept"] = concept.model_dump()
        if "topic" not in updates and concept.logline.strip():
            updates["topic"] = concept.logline.strip()
    if updates.get("setup_state") == "":
        updates["setup_state"] = None  # "" = 起步完成
    if "chat_log" in updates and len(updates["chat_log"]) > 200:
        updates["chat_log"] = updates["chat_log"][-200:]  # 防膨胀:只留最近 200 条
    for field, value in updates.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)) -> dict:
    """删除项目及其全部关联数据(级联逻辑见 deps.delete_project_cascade)。"""
    project = _get_project_or_404(db, project_id)
    deleted_chapters = delete_project_cascade(db, project)
    return {"ok": True, "deleted_chapters": deleted_chapters}


class ArchitecturePatch(BaseModel):
    core_seed: str | None = None
    character_dynamics: str | None = None
    world_building: str | None = None
    plot_architecture: str | None = None


@router.patch("/{project_id}/architecture", response_model=ArchitectureOut)
async def patch_architecture(
    project_id: int, req: ArchitecturePatch, db: Session = Depends(get_db)
):
    """手动编辑架构(工作台直接改,版本+1)。"""
    project = _get_project_or_404(db, project_id)
    arch = project.architecture
    if arch is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    updates = req.model_dump(exclude_none=True)
    if updates:
        for field, value in updates.items():
            setattr(arch, field, value)
        arch.version += 1
        db.commit()
        db.refresh(arch)
    return arch


@router.post("/{project_id}/architecture", response_model=ArchitectureOut)
async def generate_project_architecture(
    project_id: int,
    req: GenerateArchitectureRequest,
    db: Session = Depends(get_db),
):
    """雪花四步生成顶层架构(串行 4 次 LLM 调用,耗时较长)。"""
    project = _get_project_or_404(db, project_id)

    result = await generate_architecture(
        topic=project.topic,
        genre=project.genre,
        number_of_chapters=project.target_chapters,
        word_number=project.target_words_per_chapter,
        concept=project.concept,
        tendency=req.tendency,
        global_tendency=project.global_tendency,
    )
    arch = save_architecture(db, project, result)
    db.commit()
    db.refresh(arch)
    return arch


@router.post("/{project_id}/architecture-async")
async def generate_project_architecture_async(
    project_id: int,
    req: GenerateArchitectureRequest,
    db: Session = Depends(get_db),
):
    """异步生成架构:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看 1/4-4/4 进度。"""
    _get_project_or_404(db, project_id)  # 先校验存在与归属
    # 防重复提交:同项目架构任务已在跑 → 复用(前端接上轮询即可)
    for jid, _job in list_running(f"architecture-{project_id}"):
        if _job["kind"] == f"architecture-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"architecture-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            result = await generate_architecture(
                topic=project.topic,
                genre=project.genre,
                number_of_chapters=project.target_chapters,
                word_number=project.target_words_per_chapter,
                concept=project.concept,
                tendency=req.tendency,
                global_tendency=project.global_tendency,
                progress=lambda s: update_stage(job_id, s),
            )
            update_stage(job_id, "落库中")
            arch = save_architecture(session, project, result)
            session.commit()
            session.refresh(arch)
            finish_job(job_id, ArchitectureOut.model_validate(arch).model_dump())
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("/{project_id}/architecture", response_model=ArchitectureOut)
async def get_project_architecture(
    project_id: int, db: Session = Depends(get_db)
):
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    return project.architecture


@router.post("/{project_id}/blueprint", response_model=GenerateBlueprintResponse)
async def generate_project_blueprint(
    project_id: int,
    req: GenerateBlueprintRequest,
    db: Session = Depends(get_db),
):
    """基于已有架构,分块生成全书章节蓝图并落库。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(
            status_code=400, detail="请先生成顶层架构(POST .../architecture)"
        )

    from app.engines.pipeline.architecture import ArchitectureResult

    arch_text = ArchitectureResult(
        core_seed=project.architecture.core_seed,
        character_dynamics=project.architecture.character_dynamics,
        world_building=project.architecture.world_building,
        plot_architecture=project.architecture.plot_architecture,
    ).full_text

    chapters, warnings = await generate_blueprint(
        novel_architecture=arch_text,
        number_of_chapters=project.target_chapters,
        tendency=req.tendency,
        global_tendency=project.global_tendency,
    )
    outlines = save_blueprint(db, project, chapters)
    db.commit()
    return GenerateBlueprintResponse(
        outlines=[OutlineOut.model_validate(o) for o in outlines],
        warnings=warnings,
    )


@router.post("/{project_id}/blueprint-async")
async def generate_project_blueprint_async(
    project_id: int,
    req: GenerateBlueprintRequest,
    db: Session = Depends(get_db),
):
    """异步生成蓝图:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看分块进度。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(
            status_code=400, detail="请先生成顶层架构(POST .../architecture)"
        )
    # 防重复提交:同项目蓝图任务已在跑 → 复用
    for jid, _job in list_running(f"blueprint-{project_id}"):
        if _job["kind"] == f"blueprint-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"blueprint-{project_id}")

    async def runner() -> None:
        from app.engines.pipeline.architecture import ArchitectureResult

        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            arch_text = ArchitectureResult(
                core_seed=p.architecture.core_seed,
                character_dynamics=p.architecture.character_dynamics,
                world_building=p.architecture.world_building,
                plot_architecture=p.architecture.plot_architecture,
            ).full_text

            chapters, warnings = await generate_blueprint(
                novel_architecture=arch_text,
                number_of_chapters=p.target_chapters,
                tendency=req.tendency,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
            )
            update_stage(job_id, "落库中")
            outlines = save_blueprint(session, p, chapters)
            session.commit()
            finish_job(job_id, {
                "outlines": [
                    OutlineOut.model_validate(o).model_dump() for o in outlines
                ],
                "warnings": warnings,
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("/{project_id}/outlines", response_model=list[OutlineOut])
async def list_outlines(
    project_id: int, db: Session = Depends(get_db)
) -> list[Outline]:
    _get_project_or_404(db, project_id)
    return list(
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    )
