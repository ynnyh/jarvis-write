# app/api/scripts.py
# -*- coding: utf-8 -*-
"""剧本工坊 API:独立创作 + 小说改编共用一套数据与生成管线。

端点:
- POST   /api/scripts                        创建剧本(独立)
- GET    /api/scripts                        我的剧本列表
- GET    /api/scripts/{sid}                  剧本详情
- DELETE /api/scripts/{sid}                  删除
- GET    /api/scripts/{sid}/episodes         集列表
- PATCH  /api/scripts/{sid}/episodes/{n}     手改集内容
- POST   /api/scripts/{sid}/generate-outline AI 生成分集大纲(清旧重建)
- POST   /api/scripts/{sid}/episodes/{n}/generate   AI 生成单集剧本
- POST   /api/projects/{pid}/adapt-to-script        小说改编:定稿章 → 分集大纲 → 建剧本
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Chapter, Project, Script, ScriptEpisode, User
from app.db.session import get_db
from app.engines.adapt import (
    DEFAULT_SOURCE_BUDGET,
    banned_block,
    book_assets_block,
    open_threads_block,
    source_text as adapt_source_text,
)
from app.engines.script import (
    ScriptError,
    find_version,
    generate_episode as engine_generate_episode,
    generate_outline as engine_generate_outline,
    push_version,
    version_list,
)
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.script import SCRIPT_ADAPT_PROMPT

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

# 小说改编路由挂在 projects 前缀下(语义:这本书 → 改编成剧本)
adapt_router = APIRouter(prefix="/api/projects", tags=["scripts"])


class ScriptCreate(BaseModel):
    title: str = ""
    genre: str = ""
    logline: str = ""
    target_episodes: int = Field(default=12, ge=2, le=100)


class ScriptUpdate(BaseModel):
    title: str | None = None
    genre: str | None = None
    logline: str | None = None
    style_memo: str | None = None
    target_episodes: int | None = Field(default=None, ge=2, le=100)


class ScriptOut(BaseModel):
    id: int
    title: str
    genre: str
    logline: str
    target_episodes: int
    status: str
    style_memo: str = ""
    source_project_id: int | None = None


class EpisodeOut(BaseModel):
    id: int
    episode_number: int
    title: str
    synopsis: str
    opening_hook: str
    ending_hook: str
    status: str
    content: str
    word_count: int
    # 集末交接契约提取状态("ok"/"failed"/"",空=还没提取过)与历史版本数。
    # 都是 extra 里的元信息,空值不影响老前端。
    end_state_status: str = ""
    versions: int = 0


class EpisodeVersionOut(BaseModel):
    version: int
    word_count: int
    source: str
    saved_at: str
    content: str


class EpisodeGenerate(BaseModel):
    extra_direction: str = ""


class EpisodeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    status: str | None = None


class AdaptNovel(BaseModel):
    chapter_numbers: list[int] = Field(default_factory=list, description="空=全部有正文的章")
    target_episodes: int = Field(default=12, ge=2, le=60)


# ---------- 工具 ----------

def _get_script(db: Session, script_id: int, user: User) -> Script:
    row = db.query(Script).filter(Script.id == script_id, Script.user_id == user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="剧本不存在")
    return row


def _script_out(s: Script) -> ScriptOut:
    return ScriptOut(
        id=s.id, title=s.title, genre=s.genre, logline=s.logline,
        target_episodes=s.target_episodes, status=s.status,
        style_memo=s.style_memo, source_project_id=s.source_project_id,
    )


def _episode_out(e: ScriptEpisode) -> EpisodeOut:
    extra = e.extra if isinstance(e.extra, dict) else {}
    return EpisodeOut(
        id=e.id, episode_number=e.episode_number, title=e.title,
        synopsis=e.synopsis, opening_hook=e.opening_hook, ending_hook=e.ending_hook,
        status=e.status, content=e.content, word_count=e.word_count,
        end_state_status=str(extra.get("end_state_status") or ""),
        versions=len(extra.get("versions") or []),
    )


# ---------- 独立创作 CRUD ----------

@router.post("", response_model=ScriptOut)
async def create_script(
    req: ScriptCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = Script(
        user_id=user.id,
        title=req.title.strip() or "未命名剧本",
        genre=req.genre.strip(),
        logline=req.logline.strip(),
        target_episodes=req.target_episodes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _script_out(row)


@router.get("", response_model=list[ScriptOut])
async def list_scripts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(Script).filter(Script.user_id == user.id).order_by(Script.id.desc()).all()
    return [_script_out(r) for r in rows]


@router.get("/{script_id}", response_model=ScriptOut)
async def get_script(
    script_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _script_out(_get_script(db, script_id, user))


@router.patch("/{script_id}", response_model=ScriptOut)
async def update_script(
    script_id: int,
    req: ScriptUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改剧本设定(标题/类型/一句话/风格备忘/集数)。集数改动不重建集,重生成大纲时生效。"""
    s = _get_script(db, script_id, user)
    if req.title is not None:
        s.title = req.title.strip() or "未命名剧本"
    if req.genre is not None:
        s.genre = req.genre.strip()
    if req.logline is not None:
        s.logline = req.logline.strip()
    if req.style_memo is not None:
        s.style_memo = req.style_memo.strip()
    if req.target_episodes is not None:
        s.target_episodes = req.target_episodes
    db.commit()
    db.refresh(s)
    return _script_out(s)


@router.delete("/{script_id}")
async def delete_script(
    script_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_script(db, script_id, user)
    db.query(ScriptEpisode).filter(ScriptEpisode.script_id == row.id).delete()
    db.delete(row)
    db.commit()
    return {"deleted": True}


# ---------- 集:列表 / 手改 ----------

def _get_episode(db: Session, script_id: int, n: int) -> ScriptEpisode:
    row = (
        db.query(ScriptEpisode)
        .filter(ScriptEpisode.script_id == script_id, ScriptEpisode.episode_number == n)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="集不存在")
    return row


@router.get("/{script_id}/episodes", response_model=list[EpisodeOut])
async def list_episodes(
    script_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_script(db, script_id, user)
    rows = (
        db.query(ScriptEpisode)
        .filter(ScriptEpisode.script_id == script_id)
        .order_by(ScriptEpisode.episode_number)
        .all()
    )
    return [_episode_out(r) for r in rows]


@router.patch("/{script_id}/episodes/{n}", response_model=EpisodeOut)
async def update_episode(
    script_id: int,
    n: int,
    req: EpisodeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_script(db, script_id, user)
    row = _get_episode(db, script_id, n)
    if req.title is not None:
        row.title = req.title.strip()
    if req.content is not None and req.content != (row.content or ""):
        # 手改也算一版:改坏了能退回去,不用重新生成一遍
        push_version(row, source="manual")
        row.content = req.content
        row.word_count = len(req.content)
    if req.status is not None:
        row.status = req.status
    db.commit()
    db.refresh(row)
    return _episode_out(row)


@router.get("/{script_id}/episodes/{n}/versions", response_model=list[EpisodeVersionOut])
async def list_episode_versions(
    script_id: int,
    n: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """本集的历史版本(最新在前)。生成与手改前都会自动存一版。"""
    _get_script(db, script_id, user)
    row = _get_episode(db, script_id, n)
    return version_list(row)


@router.post(
    "/{script_id}/episodes/{n}/versions/{version}/restore",
    response_model=EpisodeOut,
)
async def restore_episode_version(
    script_id: int,
    n: int,
    version: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回退到某一历史版本;当前正文会先存成一版,回退本身也可再退。"""
    _get_script(db, script_id, user)
    row = _get_episode(db, script_id, n)
    target = find_version(row, version)
    if target is None:
        raise HTTPException(status_code=404, detail=f"第 {version} 版不存在")
    push_version(row, source=f"before-restore-v{version}")
    row.content = target["content"]
    row.word_count = len(target["content"])
    if row.status == "outlined":
        row.status = "drafted"
    db.commit()
    db.refresh(row)
    return _episode_out(row)


# ---------- LLM:分集大纲 ----------

@router.post("/{script_id}/generate-outline")
async def generate_outline(
    script_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 生成分集大纲:清掉旧集,按 target_episodes 重建全部集大纲。"""
    s = _get_script(db, script_id, user)
    adapter = get_adapter_for(Task.SUMMARY, max_tokens=3000, timeout=180)
    try:
        episodes = await engine_generate_outline(adapter, s)
    except ScriptError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.query(ScriptEpisode).filter(ScriptEpisode.script_id == script_id).delete()
    for i, ep in enumerate(episodes[: s.target_episodes], 1):
        db.add(ScriptEpisode(
            script_id=script_id, episode_number=int(ep.get("episode_number") or i),
            title=str(ep.get("title") or f"第{i}集")[:200],
            synopsis=str(ep.get("synopsis") or ""),
            opening_hook=str(ep.get("opening_hook") or ""),
            ending_hook=str(ep.get("ending_hook") or ""),
            status="outlined",
        ))
    s.status = "outlined"
    db.commit()
    rows = (
        db.query(ScriptEpisode)
        .filter(ScriptEpisode.script_id == script_id)
        .order_by(ScriptEpisode.episode_number)
        .all()
    )
    return {"episodes": [_episode_out(r).model_dump() for r in rows]}


# ---------- LLM:逐集剧本生成(编排在 app/engines/script/) ----------

@router.post("/{script_id}/episodes/{n}/generate", response_model=EpisodeOut)
async def generate_episode(
    script_id: int,
    n: int,
    req: EpisodeGenerate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 生成单集剧本:分集大纲 + 上一集集末契约与结尾 + 风格备忘。

    编排在 app/engines/script/:输出过不了 Fountain 格式门禁会整发重试一次,
    写成后顺带提取「集末交接契约」供下一集衔接,覆盖正文前存一版快照。
    """
    s = _get_script(db, script_id, user)
    ep = _get_episode(db, script_id, n)
    draft_adapter = get_adapter_for(Task.DRAFT, max_tokens=4000, timeout=300)
    state_adapter = get_adapter_for(Task.SUMMARY, max_tokens=2000, timeout=180)
    try:
        await engine_generate_episode(
            db, s, ep, draft_adapter, state_adapter,
            extra_direction=req.extra_direction,
        )
    except ScriptError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.refresh(ep)
    return _episode_out(ep)


# ---------- 小说改编:定稿章 → 改编蓝本 → 分集大纲 → 建剧本 ----------


@adapt_router.post("/{project_id}/adapt-to-script")
async def adapt_to_script(
    project_id: int,
    req: AdaptNovel,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """小说改编:取定稿章节压缩成改编蓝本 → 分集大纲 → 建剧本(可再逐集生成)。"""
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user.id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    query = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
    )
    if req.chapter_numbers:
        query = query.filter(Chapter.chapter_number.in_(req.chapter_numbers))
    chapters = query.all()
    if not chapters:
        raise HTTPException(status_code=400, detail="没有可改编的定稿章节,先在小说里生成正文")

    # 改编素材(2026-09-10 修):此前每章只取前 600 字纯头截断、总截 12000,
    # 一本书 80% 的内容在改编时凭空消失,且完全看不到本书基因 / 作者雷区 /
    # 章末未决线索——比更远的漫剧衍生链吃得还少。现与漫剧同一口径:保头尾去
    # 中段(结尾是卡点素材的来源)+ 书级资产 + 未决线索。
    chapter_numbers = [ch.chapter_number for ch in chapters]
    body, used = adapt_source_text(db, project_id, chapter_numbers, DEFAULT_SOURCE_BUDGET)
    prompt = SCRIPT_ADAPT_PROMPT.format(
        title=project.title, chapter_count=len(chapters),
        target_episodes=req.target_episodes, source_text=body,
        assets_block=book_assets_block(project),
        banned_block=banned_block(db, project_id),
        threads_block=open_threads_block(db, project_id, used or chapter_numbers),
    )
    adapter = get_adapter_for(Task.SUMMARY, max_tokens=4000, timeout=300)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"改编失败: {exc}") from exc
    data = parse_llm_json(raw) or {}
    episodes = data.get("episodes") or []
    if not episodes:
        raise HTTPException(status_code=502, detail="模型没有返回可用的分集大纲,请重试。")

    script = Script(
        user_id=user.id, source_project_id=project_id,
        title=f"《{project.title}》改编"[:200], genre=project.genre or "剧情",
        logline=str(data.get("logline") or project.topic or "")[:500],
        target_episodes=min(len(episodes), req.target_episodes), status="outlined",
        style_memo=str(data.get("adapt_note") or "")[:500],
    )
    db.add(script)
    db.flush()
    for i, ep in enumerate(episodes[: req.target_episodes], 1):
        db.add(ScriptEpisode(
            script_id=script.id, episode_number=i,
            title=str(ep.get("title") or f"第{i}集")[:200],
            synopsis=str(ep.get("synopsis") or ""),
            opening_hook=str(ep.get("opening_hook") or ""),
            ending_hook=str(ep.get("ending_hook") or ""),
            status="outlined",
        ))
    db.commit()
    db.refresh(script)
    return {
        "script_id": script.id, "title": script.title,
        "episodes": len(episodes), "logline": script.logline,
    }
