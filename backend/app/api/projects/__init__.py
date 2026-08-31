# app/api/projects/__init__.py
# -*- coding: utf-8 -*-
"""项目管理 + 生成流水线接口。

阶段 1 核心链路:
  POST /api/projects                          建项目(带全局倾向)
  POST /api/projects/{id}/architecture        雪花四步生成顶层架构
  POST /api/projects/{id}/architecture-async  同上,异步任务(前端轮询进度)
  POST /api/projects/{id}/blueprint           分块生成章节蓝图并落库
  POST /api/projects/{id}/blueprint-async     同上,异步任务(前端轮询进度)
  GET  /api/projects/{id}/outlines            查看章节目录

拆分自原单文件 projects.py(987 行,仅按职责分文件,路由/行为零变更):
- naming         AI 起名 / 书籍简介(同步 + 异步)
- architecture   顶层架构:生成 / 手改 / 研讨
- style_profile  创作偏好档案:读 / 写 / 吸收 / 反向提炼
- blueprint      章节蓝图:生成 / 滚动规划 / 目录查询

本文件除聚合上述子 router 外,自持项目 CRUD(create/list/get/patch/delete)。
其中 create/list 是空 path(POST ""/GET ""),FastAPI 要求挂在带 prefix 的 router
上(bare 子 router 无 prefix 会在 include 时报 "Prefix and path cannot be both
empty"),故 CRUD 全部直接定义在主 router;/{project_id} 通配放在所有子路由之后,
方法 + 段数各不相同,不遮蔽任何字面/多段子路由。

前缀 /api/projects、tags、鉴权依赖都声明在主 router 上,include_router 时 FastAPI
自动下发到各子路由,整体行为与拆分前一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import delete_project_cascade, reset_project_content
from app.auth import current_user_id, get_current_user
from app.db.models import Project
from app.db.session import get_db
from app.schemas.canon import StoryCanon
from app.schemas.concept import Concept
from app.schemas.dna import StoryDNA
from app.schemas.project import ProjectCreate, ProjectOut

from . import architecture, blueprint, naming, style_profile
from ._common import _get_project_or_404

# 向后兼容 re-export:tests/test_style_profile.py 与其他外部按
# `from app.api.projects import _parse_profile_json` 引用;列入 __all__ 表明有意导出。
from .style_profile import _parse_profile_json

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)

# 细分子功能端点(naming 的 /title-suggestion 为字面单段,其余均为 /{project_id}/... 多段)
router.include_router(naming.router)
router.include_router(architecture.router)
router.include_router(style_profile.router)
router.include_router(blueprint.router)


# —— 项目 CRUD ——
# 空 path 的 create/list 必须挂在带 prefix 的 router 上(见模块 docstring);
# /{project_id} 通配放在所有子路由之后,方法+段数各异,不遮蔽上面任何字面/多段路由。
@router.post("", response_model=ProjectOut)
async def create_project(req: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    concept_dict = None
    dna_dict = None
    topic = req.topic
    if req.concept is not None and not req.concept.is_empty():
        concept_dict = req.concept.model_dump()
        if not topic.strip() and req.concept.logline.strip():
            topic = req.concept.logline.strip()
    if req.dna is not None and not req.dna.is_empty():
        dna_dict = req.dna.model_dump()
    project = Project(
        user_id=current_user_id.get(),
        title=req.title,
        topic=topic,
        concept=concept_dict,
        dna=dna_dict,
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


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
    return _get_project_or_404(db, project_id)


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
    # 连写前置开关:True=严格(上一章 approved 才能连写),False=宽松(仅 quarantined 停)
    queue_require_approved: bool | None = None
    # 完本标记:True=已完本。完本后重命名/删除/清空被后端拦截(见 patch/delete/reset)。
    finished: bool | None = None
    global_tendency: dict | None = None
    concept: Concept | None = None
    # 故事 DNA / 本书基因(坐标卡产出):整段覆盖式保存,走通用 setattr 落 JSON 列
    dna: StoryDNA | None = None
    # 故事宪法(留白/常驻装置/倒计时):整段覆盖式保存,走通用 setattr 落 JSON 列;
    # 传空对象即清空。全书恒真声明,注入生成各环节 + 门禁比对。见 app/schemas/canon.py
    canon: StoryCanon | None = None
    synopsis: str | None = None
    # 起步流进度:传 "" 表示起步完成(落库为 NULL)
    setup_state: str | None = None
    # 灵感对话记录(整段覆盖式保存)
    chat_log: list | None = None
    # 文风备忘手动编辑:传字符串整段覆盖(传 "" 清空);不传(None)则不动
    style_memo: str | None = Field(default=None, max_length=20000)
    # 世界观硬规则(钉板):整段覆盖(传 "" 清空);逐行一条规则
    world_rules: str | None = Field(default=None, max_length=20000)
    # 出片模式:lite=轻量档(文+图出片)/ full=完整档;非法值在下方归一为 lite
    render_mode: str | None = Field(default=None, max_length=10)


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
    # 完本防误改:已完本的书不允许再改标题(重命名),须先取消完本标记。
    if project.finished and "title" in updates:
        raise HTTPException(status_code=409, detail="已标记完本,如需重命名请先取消完本标记")
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
        # 概念变了 → 标旧架构失效:重新生成架构时 save_architecture 自动复位。
        # 只在实际有架构且概念确有变化时置位,避免"重复保存同一概念"误标。
        if project.architecture is not None and concept.is_empty() is False:
            old = project.concept or {}
            changed = (
                not isinstance(old, dict)
                or any(
                    str(old.get(k) or "").strip() != getattr(concept, k).strip()
                    for k in ("logline", "hook", "twist", "protagonist", "conflict", "setting", "sell")
                )
            )
            if changed:
                project.architecture.concept_stale = True
    if updates.get("setup_state") == "":
        updates["setup_state"] = None  # "" = 起步完成
    if "chat_log" in updates and len(updates["chat_log"]) > 200:
        updates["chat_log"] = updates["chat_log"][-200:]  # 防膨胀:只留最近 200 条
    if "render_mode" in updates and updates["render_mode"] not in ("lite", "full"):
        updates["render_mode"] = "lite"  # 脏值收敛,不 400(开关打错不该炸整个保存)
    for field, value in updates.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)) -> dict:
    """删除项目及其全部关联数据(级联逻辑见 deps.delete_project_cascade)。"""
    project = _get_project_or_404(db, project_id)
    if project.finished:
        raise HTTPException(status_code=409, detail="已标记完本,如需删除请先取消完本标记")
    deleted_chapters = delete_project_cascade(db, project)
    return {"ok": True, "deleted_chapters": deleted_chapters}


@router.delete("/{project_id}/content")
async def reset_project_content_api(project_id: int, db: Session = Depends(get_db)) -> dict:
    """清空已写正文与大纲(保留架构/概念/DNA/简介),供架构重写后「从新架构重来」。

    前端大纲页在「基于旧架构」横幅里给作者这个选择;是破坏性操作,前端自带二次确认。
    """
    project = _get_project_or_404(db, project_id)
    if project.finished:
        raise HTTPException(status_code=409, detail="已标记完本,如需清空正文请先取消完本标记")
    deleted_chapters = reset_project_content(db, project)
    return {"ok": True, "deleted_chapters": deleted_chapters, "content_reset": True}


__all__ = ["router", "_get_project_or_404", "_parse_profile_json"]
