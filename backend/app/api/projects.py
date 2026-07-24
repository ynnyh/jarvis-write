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
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import delete_project_cascade
from app.auth import assert_project_owner, current_user_id, get_current_user
from app.db.models import Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.architecture import (
    discuss_architecture,
    generate_architecture,
    save_architecture,
)
from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.jobs import create_job, fail_job, finish_job, list_running, spawn_job, update_stage
from app.llm.factory import (
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.profile import PROFILE_ABSORB_PROMPT
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
async def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    uid = current_user_id.get()
    projects = list(
        db.query(Project)
        .filter(Project.user_id == uid)
        .order_by(Project.id.desc())
    )
    # 进度聚合:每项目已写章数/总字数,一条 group by 查询
    from sqlalchemy import func

    from app.db.models import Chapter

    rows = (
        db.query(
            Chapter.project_id,
            func.count(Chapter.id),
            func.coalesce(func.sum(Chapter.word_count), 0),
        )
        .filter(
            Chapter.project_id.in_([p.id for p in projects] or [0]),
            Chapter.final_content != "",
        )
        .group_by(Chapter.project_id)
        .all()
    )
    progress = {pid: (cnt, int(words)) for pid, cnt, words in rows}
    out = []
    for p in projects:
        item = ProjectOut.model_validate(p, from_attributes=True)
        item.written_chapters, item.total_words = progress.get(p.id, (0, 0))
        out.append(item)
    return out


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
    # 字数守卫开关(写作页):超标自动压缩/拆章,默认关闭
    word_guard_enabled: bool | None = None
    auto_split_enabled: bool | None = None
    # 编辑部审校把关:达标阈值(四维均需 >=,1-10)/ 自动回炉开关 / 回炉上限(0-5)
    review_pass_threshold: int | None = Field(default=None, ge=1, le=10)
    review_auto_revise: bool | None = None
    review_max_revisions: int | None = Field(default=None, ge=0, le=5)
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
        directive=req.directive,
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
                directive=req.directive,
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


class ArchDiscussRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


class ArchDiscussResponse(BaseModel):
    reply: str
    directive: str = ""


@router.post("/{project_id}/architecture/discuss", response_model=ArchDiscussResponse)
async def discuss_project_architecture(
    project_id: int,
    req: ArchDiscussRequest,
    db: Session = Depends(get_db),
):
    """就当前架构与作者多轮研讨:聊清不满意在哪 → 蒸馏出「额外要求」。

    前端拿返回的 directive 去调 architecture-async(directive 字段)重新生成。
    """
    project = _get_project_or_404(db, project_id)
    try:
        result = await discuss_architecture(
            req.messages,
            topic=project.topic,
            concept=project.concept,
            arch=project.architecture,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ArchDiscussResponse(**result)


# ---------- 创作偏好档案(贯穿全书的创作宪法,注入所有生成环节) ----------
# 档案存在 project.global_tendency["_profile"] 子字典里,复用现成的倾向拼装器
# (assemble_tendency/render_style_block)注入到生成/重写/定稿/润色/大纲/架构所有
# prompt,零新增注入点。读改写都在服务端合并,避免前端整段覆盖 global_tendency
# 时把标签倾向冲掉。
logger = logging.getLogger("jarvis-write.api")
_PROFILE_FIELDS = ("style", "taboos", "audience", "other")


def _read_profile(project: Project) -> dict:
    profile = (project.global_tendency or {}).get("_profile") or {}
    return {k: str(profile.get(k) or "") for k in _PROFILE_FIELDS}


def _write_profile(project: Project, profile: dict) -> dict:
    """合并写回 global_tendency._profile(保留其余倾向标签),返回规范化后的档案。"""
    cleaned = {k: str(profile.get(k) or "").strip() for k in _PROFILE_FIELDS}
    tendency = dict(project.global_tendency or {})
    if any(cleaned.values()):
        tendency["_profile"] = cleaned
    else:
        tendency.pop("_profile", None)  # 全空则去掉键,注入时该块整体省略
    project.global_tendency = tendency
    return cleaned


def _parse_profile_json(raw: str) -> dict:
    """从模型输出里抠出档案 JSON(容忍代码块包裹与前后多余文字)。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("档案不是 JSON 对象")
    return {k: str(obj.get(k) or "") for k in _PROFILE_FIELDS}


class StyleProfileOut(BaseModel):
    style: str = ""
    taboos: str = ""
    audience: str = ""
    other: str = ""


@router.get("/{project_id}/style-profile", response_model=StyleProfileOut)
async def get_style_profile(project_id: int, db: Session = Depends(get_db)):
    """读取这本书的创作偏好档案(未设置时四字段皆空)。"""
    project = _get_project_or_404(db, project_id)
    return StyleProfileOut(**_read_profile(project))


class StyleProfileUpdate(BaseModel):
    style: str | None = None
    taboos: str | None = None
    audience: str | None = None
    other: str | None = None


@router.put("/{project_id}/style-profile", response_model=StyleProfileOut)
async def update_style_profile(
    project_id: int, req: StyleProfileUpdate, db: Session = Depends(get_db)
):
    """保存创作偏好档案:传了的字段(含空串)覆盖,未传的沿用现值。"""
    project = _get_project_or_404(db, project_id)
    current = _read_profile(project)
    for k, v in req.model_dump().items():
        if v is not None:
            current[k] = v
    cleaned = _write_profile(project, current)
    db.commit()
    return StyleProfileOut(**cleaned)


class StyleProfileAbsorbRequest(BaseModel):
    directive: str = Field(min_length=1, max_length=2000, description="对话蒸馏出的创作主张")


@router.post("/{project_id}/style-profile/absorb", response_model=StyleProfileOut)
async def absorb_style_profile(
    project_id: int, req: StyleProfileAbsorbRequest, db: Session = Depends(get_db)
):
    """把对话里聊出的创作主张,用 LLM 归类合并进档案对应字段后保存。

    吸收失败(模型/解析异常)时降级:把原文并进「其他创作主张」,不丢用户想法。
    """
    project = _get_project_or_404(db, project_id)
    current = _read_profile(project)
    directive = req.directive.strip()
    try:
        adapter = get_adapter_for(Task.SUMMARY)
        prompt = PROFILE_ABSORB_PROMPT.format(
            style=current["style"] or "(空)",
            taboos=current["taboos"] or "(空)",
            audience=current["audience"] or "(空)",
            other=current["other"] or "(空)",
            directive=directive,
        )
        merged = _parse_profile_json(await adapter.ask(prompt))
        cleaned = _write_profile(project, merged)
    except Exception:  # noqa: BLE001 — 降级:并进其他主张,不阻塞
        logger.warning("档案吸收失败,降级并入其他主张", exc_info=True)
        other = current["other"]
        current["other"] = f"{other};{directive}".strip("; ") if other else directive
        cleaned = _write_profile(project, current)
    db.commit()
    return StyleProfileOut(**cleaned)


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
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            arch_text = _arch_text(p)
            # 滚动规划:长书只铺第一卷,先出卷纲定全书方向;短书一次铺完
            end_chapter = None
            if p.target_chapters > ROLLING_THRESHOLD:
                style_block = render_style_block(
                    assemble_tendency("outline", req.tendency, p.global_tendency)
                )
                update_stage(job_id, "生成全书卷纲(指南针)")
                segments = await _ensure_macro_plan(session, p, style_block)
                end_chapter = min(segments[0]["end"], p.target_chapters)

            chapters, warnings = await generate_blueprint(
                novel_architecture=arch_text,
                number_of_chapters=p.target_chapters,
                tendency=req.tendency,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
                end_chapter=end_chapter,
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


# ---------- 滚动规划:卷纲 + 分段蓝图 ----------

# 每卷章数与启用阈值:目标超过阈值的书走滚动规划(首铺一卷,写到卷尾再展开)
SEGMENT_SIZE = 30
ROLLING_THRESHOLD = 40


def _arch_text(p: Project) -> str:
    from app.engines.pipeline.architecture import ArchitectureResult

    return ArchitectureResult(
        core_seed=p.architecture.core_seed,
        character_dynamics=p.architecture.character_dynamics,
        world_building=p.architecture.world_building,
        plot_architecture=p.architecture.plot_architecture,
    ).full_text


async def _ensure_macro_plan(session, p: Project, style_block: str) -> list[dict]:
    """卷纲缺失时生成一次(指南针,全书方向锚点)。幂等。"""
    import math

    from app.engines.consistency.extractor import parse_llm_json
    from app.prompts.rolling import MACRO_PLAN_PROMPT

    if p.macro_plan:
        return p.macro_plan
    segment_count = math.ceil(p.target_chapters / SEGMENT_SIZE)
    prompt = MACRO_PLAN_PROMPT.format(
        number_of_chapters=p.target_chapters,
        novel_architecture=_arch_text(p),
        style_directives=style_block,
        segment_count=segment_count,
        segment_size=SEGMENT_SIZE,
    )
    raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
    data = parse_llm_json(raw)
    segments = []
    cursor = 1
    for seg in (data.get("segments") or [])[:segment_count]:
        if not isinstance(seg, dict) or not str(seg.get("goal") or "").strip():
            continue
        end = min(int(seg.get("end") or (cursor + SEGMENT_SIZE - 1)), p.target_chapters)
        if end < cursor:
            continue
        segments.append({"start": cursor, "end": end, "goal": str(seg["goal"]).strip()})
        cursor = end + 1
    # 模型没铺满目标章数:兜底补一卷到结尾
    if cursor <= p.target_chapters:
        segments.append({
            "start": cursor, "end": p.target_chapters,
            "goal": "收束全部主线与伏笔,完成架构中的终局。",
        })
    if not segments:
        raise RuntimeError("卷纲生成失败(模型输出无法解析),请重试。")
    p.macro_plan = segments
    session.commit()
    return segments


def _segment_for(segments: list[dict], chapter: int) -> tuple[dict, dict | None]:
    """chapter 所在卷及下一卷(无则 None)。"""
    for i, seg in enumerate(segments):
        if seg["start"] <= chapter <= seg["end"]:
            return seg, segments[i + 1] if i + 1 < len(segments) else None
    return segments[-1], None


def _written_state_block(session, p: Project, seg: dict, next_seg: dict | None) -> str:
    """展开下一卷时注入的已成文状态(前情摘要 + 未回收伏笔 + 卷目标)。"""
    from app.db.models import Chapter, Foreshadowing
    from app.db.models.summary import ChapterSummary
    from app.prompts.rolling import ROLLING_CONTEXT_BLOCK

    last_written = (
        session.query(Chapter)
        .filter(Chapter.project_id == p.id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number.desc())
        .first()
    )
    written_upto = last_written.chapter_number if last_written else 0
    srow = (
        session.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == p.id,
            ChapterSummary.chapter_number == written_upto,
        )
        .first()
        if written_upto
        else None
    )
    rolling = (srow.rolling_summary if srow else "") or "(尚无成文,按蓝图衔接上下卷)"
    fores = (
        session.query(Foreshadowing)
        .filter(
            Foreshadowing.project_id == p.id,
            Foreshadowing.status.in_(("planted", "reinforced")),
        )
        .order_by(Foreshadowing.chapter_planted)
        .limit(20)
        .all()
    )
    fore_lines = "\n".join(
        f"- 「{f.description}」(第{f.chapter_planted}章埋,预期第{f.expected_payoff_chapter or '?'}章收)"
        for f in fores
    ) or "(无)"
    return ROLLING_CONTEXT_BLOCK.format(
        start=seg["start"], end=seg["end"], segment_goal=seg["goal"],
        next_goal=(next_seg["goal"] if next_seg else "(已是最终卷,收束全书)"),
        written_upto=written_upto, rolling_summary=rolling[:2500],
        open_foreshadows=fore_lines,
    )


@router.post("/{project_id}/blueprint-extend-async")
async def extend_blueprint_async(project_id: int, db: Session = Depends(get_db)):
    """展开下一卷蓝图:按卷纲 + 已成文状态规划下一段章节。滚动规划核心。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=400, detail="请先生成顶层架构")
    max_outline = (
        db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number.desc())
        .first()
    )
    planned_upto = max_outline[0] if max_outline else 0
    if planned_upto == 0:
        raise HTTPException(status_code=400, detail="还没有首卷蓝图,请先「生成蓝图」")
    if planned_upto >= project.target_chapters:
        raise HTTPException(status_code=400, detail="全书蓝图已铺满,无需展开")
    for jid, _job in list_running(f"blueprint-{project_id}"):
        if _job["kind"] == f"blueprint-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"blueprint-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            style_block = render_style_block(
                assemble_tendency("outline", {}, p.global_tendency)
            )
            update_stage(job_id, "读取卷纲与前情状态")
            segments = await _ensure_macro_plan(session, p, style_block)
            start = planned_upto + 1
            seg, next_seg = _segment_for(segments, start)
            end = min(seg["end"], p.target_chapters)
            context = _written_state_block(session, p, seg, next_seg)
            # 上一卷蓝图尾部:衔接用
            tail_outlines = (
                session.query(Outline)
                .filter(Outline.project_id == project_id)
                .order_by(Outline.chapter_number.desc())
                .limit(4)
                .all()
            )
            prev_tail = "\n".join(
                f"第{o.chapter_number}章 {o.title}:{o.summary}"
                for o in reversed(tail_outlines)
            )
            chapters, warnings = await generate_blueprint(
                novel_architecture=_arch_text(p) + context,
                number_of_chapters=p.target_chapters,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
                start_chapter=start,
                end_chapter=end,
                previous_tail=prev_tail,
            )
            update_stage(job_id, "落库中")
            outlines = save_blueprint(session, p, chapters)
            session.commit()
            finish_job(job_id, {
                "outlines": [OutlineOut.model_validate(o).model_dump() for o in outlines],
                "warnings": warnings,
                "planned_range": [start, end],
            })
        except Exception as exc:  # noqa: BLE001
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
