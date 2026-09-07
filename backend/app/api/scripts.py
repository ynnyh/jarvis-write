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
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for

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
    return EpisodeOut(
        id=e.id, episode_number=e.episode_number, title=e.title,
        synopsis=e.synopsis, opening_hook=e.opening_hook, ending_hook=e.ending_hook,
        status=e.status, content=e.content, word_count=e.word_count,
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
    if req.content is not None:
        row.content = req.content
        row.word_count = len(req.content)
    if req.status is not None:
        row.status = req.status
    db.commit()
    db.refresh(row)
    return _episode_out(row)


# ---------- LLM:分集大纲 ----------

_OUTLINE_PROMPT = """你是资深电视剧编剧。根据下面的剧本设定,生成分集大纲。

【剧名】{title}
【类型】{genre}
【一句话故事】{logline}
【总集数】{target_episodes} 集
{seed_block}
要求:
1. 恰好 {target_episodes} 集,每集包含:集名(2-8字)、本集梗概(60-120字)、开场钩子(一句)、结尾钩子(一句)
2. 主线贯穿全部集数,有整体爬升感;每集有独立小冲突
3. 集与集之间因果关系清楚,结尾钩子勾着观众看下一集

严格输出 JSON(不要 markdown 围栏):
{{"episodes": [{{"episode_number": 1, "title": "集名", "synopsis": "梗概", "opening_hook": "开场钩子", "ending_hook": "结尾钩子"}}]}}
"""


@router.post("/{script_id}/generate-outline")
async def generate_outline(
    script_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 生成分集大纲:清掉旧集,按 target_episodes 重建全部集大纲。"""
    s = _get_script(db, script_id, user)
    seed_block = f"【灵感种子】{s.logline}\n" if s.logline.strip() else ""
    prompt = _OUTLINE_PROMPT.format(
        title=s.title, genre=s.genre or "剧情", logline=s.logline or "(未填)",
        target_episodes=s.target_episodes, seed_block=seed_block,
    )
    adapter = get_adapter_for(Task.SUMMARY, max_tokens=3000, timeout=180)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"分集大纲生成失败: {exc}") from exc
    data = parse_llm_json(raw) or {}
    episodes = data.get("episodes") or []
    if not episodes:
        raise HTTPException(status_code=502, detail="模型没有返回可用的大纲,请重试。")

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


# ---------- LLM:逐集剧本生成 ----------

_EPISODE_PROMPT = """你是职业电视剧编剧,撰写第 {n} 集「{title}」的完整剧本。

【类型】{genre}
【本集梗概】{synopsis}
【开场钩子】{opening_hook}
【结尾钩子】{ending_hook}
{prev_block}{memo_block}{extra_block}
格式(Fountain 风格,纯文本):
场景标题行:内景/外景 · 日/夜 · 地点
动作行:现在时态描写,只写可见可听的
对白:人物名独立一行居中,下一行是台词;括号内注语气

要求:
1. 只写本集内容,不越界到其他集
2. 场景 3-6 个,每个场景有明确的地点与时间变化
3. 对白要有潜台词,不要直接说明情绪
4. 结尾必须落在结尾钩子上

直接输出剧本正文,不要解释:
"""


@router.post("/{script_id}/episodes/{n}/generate", response_model=EpisodeOut)
async def generate_episode(
    script_id: int,
    n: int,
    req: EpisodeGenerate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 生成单集剧本:基于分集大纲 + 前集结尾 + 风格备忘。"""
    s = _get_script(db, script_id, user)
    ep = _get_episode(db, script_id, n)
    prev = (
        db.query(ScriptEpisode)
        .filter(ScriptEpisode.script_id == script_id, ScriptEpisode.episode_number < n)
        .order_by(ScriptEpisode.episode_number.desc())
        .first()
    )
    prev_block = ""
    if prev is not None and prev.content:
        prev_block = f"【上一集结尾(衔接用,只取最后 400 字)】\n{prev.content[-400:]}\n"
    memo = s.style_memo or ""
    memo_block = f"【剧本文风备忘】\n{memo}\n" if memo.strip() else ""
    extra_block = f"【用户补充方向】\n{req.extra_direction}\n" if req.extra_direction.strip() else ""
    prompt = _EPISODE_PROMPT.format(
        n=n, title=ep.title or s.title, genre=s.genre or "剧情",
        synopsis=ep.synopsis or ep.title, opening_hook=ep.opening_hook,
        ending_hook=ep.ending_hook, prev_block=prev_block, memo_block=memo_block,
        extra_block=extra_block,
    )
    adapter = get_adapter_for(Task.DRAFT, max_tokens=4000, timeout=300)
    try:
        content = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"剧本生成失败: {exc}") from exc
    if not content.strip():
        raise HTTPException(status_code=502, detail="模型没有返回剧本内容,请重试。")
    ep.content = content
    ep.word_count = len(content)
    ep.status = "drafted"
    db.commit()
    db.refresh(ep)
    return _episode_out(ep)


# ---------- 小说改编:定稿章 → 改编蓝本 → 分集大纲 → 建剧本 ----------

_ADAPT_PROMPT = """你是资深电视剧编剧兼改编顾问。把下面的小说定稿章节改编成 {target_episodes} 集的剧本分集大纲。

【原著】{title}({chapter_count} 章)
{source_text}

改编要求:
1. 保留主线冲突与人物弧光;必要的取舍写进 "adapt_note"
2. 恰好 {target_episodes} 集,每集:集名/梗概(60-120字)/开场钩子/结尾钩子
3. "logline" 用一句话概括整部剧

严格输出 JSON(不要 markdown 围栏):
{{"logline": "一句话", "adapt_note": "取舍说明", "episodes": [{{"episode_number": 1, "title": "集名", "synopsis": "梗概", "opening_hook": "钩子", "ending_hook": "钩子"}}]}}
"""


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

    source_text = "\n".join(
        f"第{ch.chapter_number}章:{(ch.final_content or '')[:600]}" for ch in chapters
    )[:12000]
    prompt = _ADAPT_PROMPT.format(
        title=project.title, chapter_count=len(chapters),
        target_episodes=req.target_episodes, source_text=source_text,
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
