# app/api/anime.py
# -*- coding: utf-8 -*-
"""动画短剧接口:固定卡司的 60-90 秒原创系列动画,类型自选,按集出梗出提示词。

GET    /api/anime/meta                                    类型/画风/时长档/切段目录
POST   /api/anime/suggest-premise                         没灵感:AI 出三个系列设定点子(同步)
GET    /api/anime                                         我的系列列表
POST   /api/anime                                         建系列
GET    /api/anime/{sid}                                   系列详情(含剧集列表)
PATCH  /api/anime/{sid}                                   改系列设定
DELETE /api/anime/{sid}                                   删系列(级联删集)
POST   /api/anime/{sid}/cast                              AI 设计卡司(job;locked 保留)
PUT    /api/anime/{sid}/cast                              手改卡司保存
POST   /api/anime/{sid}/episodes                          新建集 {premise}
POST   /api/anime/{sid}/suggest-episode                   没灵感:AI 出三个下一集命题(同步)
PATCH  /api/anime/episodes/{eid}                          改命题/标题
DELETE /api/anime/episodes/{eid}                          删集(生成中 409)
POST   /api/anime/episodes/{eid}/takes                    出三梗纲(job,没点子的捷径)
POST   /api/anime/episodes/{eid}/pick                     选定梗纲(=确认简介) {index}
POST   /api/anime/episodes/{eid}/chat                     点子聊天:AI 补充完善出简介(同步)
POST   /api/anime/episodes/{eid}/confirm-synopsis         用户拍板确认简介,解锁分镜
POST   /api/anime/episodes/{eid}/shots                    展开分镜(job,需已确认简介)
PUT    /api/anime/episodes/{eid}/shots                    手改分镜保存
POST   /api/anime/episodes/{eid}/film-prompt              整集分段提示词(job, {segment_s})
GET    /api/anime/episodes/{eid}/film-prompt              读提示词
PUT    /api/anime/episodes/{eid}/film-prompt              整段替换保存(手改/自贴)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, get_current_user
from app.db.models import AnimeEpisode, AnimeSeries
from app.db.session import get_db
from app.engines.anime import (
    MAX_SHOTS,
    AnimeError,
    PREMISE_MAX,
    TITLE_MAX,
    VALID_EPISODE_S,
    anime_chat,
    build_film_prompt,
    confirm_synopsis,
    episode_dict,
    gen_shots,
    gen_takes,
    generate_cast,
    genre_of,
    norm_cast,
    pick_take,
    save_cast,
    save_shots,
    series_dict,
    suggest_episode_premises,
    suggest_series_premises,
    valid_genres,
)
from app.engines.media.directions import DIRECTIONS, VALID_DIRECTIONS, direction_directive
from app.engines.anime.screenplay import episode_cast, gen_script, invalidate_script, save_script, validate_script_shots
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.anime")

VALID_WORKSPACES = {"anime", "original"}


async def _workspace_boundary(request: Request, workspace: str = Query("anime"), db: Session = Depends(get_db)):
    """每个读写/生成端点都校验工作区，不能靠隐藏列表实现隔离。"""
    _check_workspace(workspace)
    if "eid" in request.path_params:
        _ep, series = _get_episode(db, int(request.path_params["eid"]))
    elif "sid" in request.path_params:
        series = _get_series(db, int(request.path_params["sid"]))
    else:
        return
    if series.workspace != workspace:
        raise HTTPException(404, "作品不属于当前工作区")


router = APIRouter(prefix="/api/anime", tags=["anime"], dependencies=[Depends(get_current_user), Depends(_workspace_boundary)])


# ---- 入参模型 ---------------------------------------------

class SuggestPremiseIn(BaseModel):
    """系列设定点子:按类型出;选一个填进设定框,仍走正常建系列流程。"""
    genre: str = "comedy"


class SeriesCreateIn(BaseModel):
    title: str = Field(default="", max_length=TITLE_MAX)
    premise: str = Field(default="", max_length=PREMISE_MAX)
    genre: str = "comedy"
    direction: str = "chibi"
    episode_s: int = 60
    workspace: str = "anime"


class SeriesPatchIn(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    premise: str | None = Field(default=None, max_length=PREMISE_MAX)
    genre: str | None = None
    direction: str | None = None
    style_cn: str | None = None
    episode_s: int | None = None


class CastIn(BaseModel):
    """卡司手改保存:整卡替换(locked 由前端一并带上,归一不丢)。"""
    cast: list = []


class EpisodeCreateIn(BaseModel):
    premise: str = Field(default="", max_length=PREMISE_MAX)


class EpisodePatchIn(BaseModel):
    premise: str | None = Field(default=None, max_length=PREMISE_MAX)
    title: str | None = Field(default=None, max_length=60)


class PickIn(BaseModel):
    index: int = Field(ge=0, le=2)


class ChatIn(BaseModel):
    """点子聊天:用户一句话(可空串=让 AI 先开一轮);同步长调用。"""
    message: str = Field(default="", max_length=500)


class SynopsisIn(BaseModel):
    """确认简介:不传文本则确认当前草稿;确认后分镜按钮解锁。"""
    synopsis: str | None = None


class ShotsIn(BaseModel):
    """分镜手改保存:整卡替换,seq 重排;提示词随之作废。"""
    shots: list = []


class FilmPromptGenIn(BaseModel):
    """整集提示词生成参数:单段时长上限(外部模型单次生成的现实上限)。"""
    segment_s: int = Field(default=15)


class FilmPromptIn(BaseModel):
    """整集提示词手动保存:整段替换。"""
    film_prompt: str = ""


class ScriptIn(BaseModel):
    script: dict = Field(default_factory=dict)
    feedback: str = Field(default="", max_length=1000)


class GuestsIn(BaseModel):
    guests: list[dict] = Field(default_factory=list, max_length=3)


# ---- 校验/取行 ---------------------------------------------

def _check_genre(genre: str) -> str:
    if genre not in valid_genres():
        raise HTTPException(status_code=400, detail="类型不在目录里,请从下拉里选。")
    return genre


def _check_direction(direction: str) -> str:
    if direction not in VALID_DIRECTIONS or direction == "auto":
        raise HTTPException(status_code=400, detail="选一个具体画风方向(不支持「AI 按书定」)。")
    return direction


def _check_episode_s(episode_s: int) -> int:
    if episode_s not in VALID_EPISODE_S:
        raise HTTPException(
            status_code=400,
            detail=f"每集时长只支持 {' / '.join(str(s) for s in VALID_EPISODE_S)} 秒。",
        )
    return episode_s


def _check_workspace(workspace: str) -> str:
    if workspace not in VALID_WORKSPACES:
        raise HTTPException(status_code=400, detail="未知的动画工作区")
    return workspace


def _get_series(db: Session, sid: int) -> AnimeSeries:
    row = db.get(AnimeSeries, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="系列不存在")
    assert_project_owner(row)
    return row


def _get_episode(db: Session, eid: int) -> tuple[AnimeEpisode, AnimeSeries]:
    row = db.get(AnimeEpisode, eid)
    if row is None:
        raise HTTPException(status_code=404, detail="这一集不存在")
    assert_project_owner(row)
    series = _get_series(db, row.series_id)
    return row, series


def _episode_busy(eid: int) -> bool:
    """这集是否有生成任务在跑(删改前查)。"""
    return any(
        job["kind"] in (f"anime-takes-{eid}", f"anime-script-{eid}", f"anime-shots-{eid}", f"anime-fp-{eid}")
        for _jid, job in list_running("anime-")
    )


def _idle(eid: int):
    if _episode_busy(eid):
        raise HTTPException(409, "这一集正在生成，完成后再修改")


def _current(ep):
    if ep.creative_stale:
        raise HTTPException(409, "方向或设定已更新，请先重新打磨本集")


# ---- 端点:目录 / 系列 ---------------------------------------------

@router.get("/meta")
def meta():
    """目录一次下发:类型节奏库、画风方向(去 auto)、每集时长档、切段上限。"""
    return {
        "genres": [
            {"key": g["key"], "label": g["label"], "framing": g["framing"]}
            for g in map(genre_of, valid_genres())
        ],
        "directions": [
            {"key": d["key"], "label": d["label"], "tip": d.get("tip", "")}
            for d in DIRECTIONS if d["key"] != "auto"
        ],
        "episode_s": list(VALID_EPISODE_S),
        "segment_s": [15, 30],
        "max_shots": MAX_SHOTS,
    }


@router.post("/suggest-premise")
async def suggest_series_premises_route(body: SuggestPremiseIn):
    """没灵感:按类型出 3 个一句话系列设定点子(不落库,选中由前端回填)。"""
    genre = _check_genre(body.genre)
    try:
        return {"premises": await suggest_series_premises(genre)}
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_series(workspace: str = Query("anime"), db: Session = Depends(get_db)):
    from app.auth import current_user_id

    _check_workspace(workspace)
    rows = (
        db.query(AnimeSeries)
        .filter(AnimeSeries.user_id == current_user_id.get(), AnimeSeries.workspace == workspace)
        .order_by(AnimeSeries.updated_at.desc())
        .all()
    )
    return {"series": [series_dict(r) for r in rows]}


@router.post("")
def create_series(body: SeriesCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    genre = _check_genre(body.genre)
    direction = _check_direction(body.direction)
    episode_s = _check_episode_s(body.episode_s)
    workspace = _check_workspace(body.workspace)
    if workspace == "original" and not body.premise.strip():
        raise HTTPException(400, "先写一句原创漫剧设定")
    row = AnimeSeries(
        user_id=current_user_id.get(),
        title=(body.title or "未命名系列").strip()[:TITLE_MAX],
        premise=body.premise.strip()[:PREMISE_MAX],
        genre=genre,
        direction=direction,
        # 画风锚默认取方向硬约束,可手改——生成时逐字注入
        style_cn=direction_directive(direction),
        episode_s=episode_s,
        workspace=workspace,
        creative_goal={
            "enabled": True, "version": 1, "form": "sketch", "intent": body.premise.strip(),
            "references": [], "selected": [], "observations": [], "unknowns": [], "must": "", "avoid": "",
        } if workspace == "original" else None,
        status="cast_empty",
    )
    db.add(row)
    db.commit()
    return {"series": series_dict(row)}


@router.get("/{sid}")
def get_series(sid: int, db: Session = Depends(get_db)):
    row = _get_series(db, sid)
    episodes = (
        db.query(AnimeEpisode)
        .filter(AnimeEpisode.series_id == sid)
        .order_by(AnimeEpisode.seq.desc())
        .all()
    )
    return {"series": series_dict(row), "episodes": [episode_dict(e) for e in episodes]}


@router.patch("/{sid}")
def patch_series(sid: int, body: SeriesPatchIn, db: Session = Depends(get_db)):
    row = _get_series(db, sid)
    eps = db.query(AnimeEpisode).filter_by(series_id=sid).all()
    if any(_episode_busy(e.id) for e in eps):
        raise HTTPException(409, "系列中有一集正在生成，完成后再改设定")
    changed = any(getattr(row, k) != v for k, v in body.model_dump(exclude_none=True).items())
    if body.title is not None:
        row.title = (body.title or "未命名系列").strip()[:TITLE_MAX]
    if body.premise is not None:
        row.premise = body.premise.strip()[:PREMISE_MAX]
    if body.genre is not None:
        row.genre = _check_genre(body.genre)
    if body.direction is not None:
        row.direction = _check_direction(body.direction)
    if body.style_cn is not None:
        row.style_cn = body.style_cn.strip()[:2000]
    if body.episode_s is not None:
        row.episode_s = _check_episode_s(body.episode_s)
    if changed:
        for ep in eps:
            ep.creative_stale = True
            ep.takes = [{**t, "_stale": True} for t in (ep.takes or [])]
    db.commit()
    return {"series": series_dict(row)}


@router.delete("/{sid}")
def delete_series(sid: int, db: Session = Depends(get_db)):
    row = _get_series(db, sid)
    if any(_episode_busy(e.id) for e in db.query(AnimeEpisode).filter_by(series_id=sid)):
        raise HTTPException(409, "剧集正在生成，完成后再删除系列")
    if any(job["kind"] == f"anime-cast-{sid}" for _jid, job in list_running("anime-")):
        raise HTTPException(status_code=409, detail="卡司正在生成,等它跑完再删。")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---- 端点:卡司 ---------------------------------------------

@router.post("/{sid}/cast")
async def build_cast(sid: int, db: Session = Depends(get_db)):
    """AI 设计卡司(job):locked 角色原样保留,其余换新提案。"""
    series = _get_series(db, sid)
    if any(_episode_busy(e.id) for e in db.query(AnimeEpisode).filter_by(series_id=sid)):
        raise HTTPException(409, "剧集正在生成，完成后再重出卡司")
    kind = f"anime-cast-{sid}"
    for jid, job in list_running("anime-"):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            row = session.get(AnimeSeries, sid)
            if row is None:
                raise ValueError("系列已被删除,任务取消。")
            return {"cast": await generate_cast(session, row, progress)}

    return {"job_id": spawn_job(kind, work)}


@router.put("/{sid}/cast")
def put_cast(sid: int, body: CastIn, db: Session = Depends(get_db)):
    row = _get_series(db, sid)
    eps = db.query(AnimeEpisode).filter_by(series_id=sid).all()
    if any(_episode_busy(e.id) for e in eps) or any(j["kind"] == f"anime-cast-{sid}" for _, j in list_running("anime-")):
        raise HTTPException(409, "系列正在生成，完成后再改卡司")
    try:
        old_cast = row.cast
        row.cast = save_cast(row, body.cast)
        if old_cast != row.cast:
            for ep in eps:
                ep.creative_stale = True
                ep.takes = [{**t, "_stale": True} for t in (ep.takes or [])]
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"series": series_dict(row)}


# ---- 端点:剧集 ---------------------------------------------

@router.post("/{sid}/suggest-episode")
async def suggest_episode_premises_route(sid: int, db: Session = Depends(get_db)):
    """没灵感:按卡司+类型出 5 个单集命题卡(避开已用过的;不落库)。"""
    series = _get_series(db, sid)
    used = [
        row.premise
        for row in db.query(AnimeEpisode)
        .filter(AnimeEpisode.series_id == sid, AnimeEpisode.premise != "")
        .all()
    ]
    try:
        ideas = await suggest_episode_premises(series, used=used)
        # premises 保留给旧客户端,新客户端使用带冲突与收束信息的 ideas。
        return {"ideas": ideas, "premises": [idea["premise"] for idea in ideas]}
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{sid}/episodes")
def create_episode(sid: int, body: EpisodeCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    series = _get_series(db, sid)
    if not (series.cast or []):
        raise HTTPException(status_code=400, detail="先给系列定好卡司,再开新的一集。")
    last = (
        db.query(AnimeEpisode)
        .filter(AnimeEpisode.series_id == sid)
        .order_by(AnimeEpisode.seq.desc())
        .first()
    )
    row = AnimeEpisode(
        user_id=current_user_id.get(),
        series_id=sid,
        seq=(last.seq + 1) if last else 1,
        premise=body.premise.strip()[:PREMISE_MAX],
    )
    db.add(row)
    db.commit()
    return {"episode": episode_dict(row)}


@router.patch("/episodes/{eid}")
def patch_episode(eid: int, body: EpisodePatchIn, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    if _episode_busy(eid):
        raise HTTPException(status_code=409, detail="这一集有生成任务在跑,稍后再改。")
    if body.premise is not None:
        if row.premise != body.premise.strip()[:PREMISE_MAX]:
            row.synopsis_ok = 0
            row.creative_stale = True
            invalidate_script(row)
        row.premise = body.premise.strip()[:PREMISE_MAX]
    if body.title is not None:
        row.title = body.title.strip()[:60]
    db.commit()
    return {"episode": episode_dict(row)}


@router.delete("/episodes/{eid}")
def delete_episode(eid: int, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    if _episode_busy(eid):
        raise HTTPException(status_code=409, detail="这一集有生成任务在跑,等它跑完再删。")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/episodes/{eid}/takes")
async def build_takes(eid: int, db: Session = Depends(get_db)):
    """命题 → 三个梗纲(job)。重出会整组换新,已选定的梗纲要先重选。"""
    episode, series = _get_episode(db, eid)
    kind = f"anime-takes-{eid}"
    for jid, job in list_running("anime-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    _idle(eid)

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            ep = session.get(AnimeEpisode, eid)
            if ep is None:
                raise ValueError("这一集已被删除,任务取消。")
            sr = session.get(AnimeSeries, ep.series_id)
            return await gen_takes(session, sr, ep, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/episodes/{eid}/pick")
def pick(eid: int, body: PickIn, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    _idle(eid)
    if row.creative_stale and (body.index >= len(row.takes or []) or row.takes[body.index].get("_stale") or row.takes[body.index].get("_goal_version") != (_series.creative_goal or {}).get("version", 0)):
        raise HTTPException(409, "这些梗纲属于旧方向，请先重新出梗纲")
    try:
        ep = pick_take(row, body.index)
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"episode": ep}


@router.post("/episodes/{eid}/chat")
async def chat(eid: int, body: ChatIn, db: Session = Depends(get_db)):
    """点子聊天:AI 接住用户的话,补充完善出当前完整版简介(同步长调用)。"""
    row, series = _get_episode(db, eid)
    if _episode_busy(eid):
        raise HTTPException(status_code=409, detail="这一集有生成任务在跑,等它跑完再聊。")
    try:
        return await anime_chat(db, series, row, body.message)
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/episodes/{eid}/confirm-synopsis")
def confirm_synopsis_route(eid: int, body: SynopsisIn | None = None, db: Session = Depends(get_db)):
    """用户拍板:简介定稿、分镜解锁;传了文本就一并替换(手改过的简介也走这里)。"""
    row, _series = _get_episode(db, eid)
    _idle(eid)
    if row.creative_stale and (not body or not body.synopsis):
        raise HTTPException(409, "方向或命题已更新，请重新打磨简介，或提交按新方向修改的简介")
    try:
        ep = confirm_synopsis(row, body.synopsis if body else None)
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"episode": ep}


@router.post("/episodes/{eid}/script")
async def build_script(eid: int, body: ScriptIn | None = None, db: Session = Depends(get_db)):
    ep, series = _get_episode(db, eid)
    kind = f"anime-script-{eid}"
    for jid, job in list_running("anime-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    _idle(eid)
    if ep.creative_stale or not ep.synopsis_ok:
        raise HTTPException(409, "先按当前方向打磨并确认简介")
    feedback = body.feedback if body else ""

    async def work(progress):
        from app.db.session import SessionLocal
        with SessionLocal() as session:
            row = session.get(AnimeEpisode, eid)
            if row is None:
                raise ValueError("这一集已删除")
            sr = session.get(AnimeSeries, row.series_id)
            return await gen_script(session, sr, row, progress, feedback)

    return {"job_id": spawn_job(kind, work)}


@router.put("/episodes/{eid}/script")
def put_script(eid: int, body: ScriptIn, db: Session = Depends(get_db)):
    ep, series = _get_episode(db, eid)
    _idle(eid)
    if ep.creative_stale or not ep.synopsis_ok:
        raise HTTPException(409, "先按当前方向确认简介")
    try:
        result = save_script(ep, series, body.script)
    except AnimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"episode": result}


@router.put("/episodes/{eid}/guests")
def put_guests(eid: int, body: GuestsIn, db: Session = Depends(get_db)):
    ep, series = _get_episode(db, eid)
    _idle(eid)
    names = {c["name"] for c in series.cast or []}
    guests = []
    for guest in body.guests:
        if not guest.get("name") or not guest.get("appearance"):
            raise HTTPException(400, "客串角色需要名字与定妆描述")
        item = norm_cast([guest])[0]
        if item["name"] in names:
            raise HTTPException(400, "客串与卡司名字不能重复")
        names.add(item["name"])
        item["role"] = "客串"
        guests.append(item)
    ep.guests = guests
    invalidate_script(ep)
    db.commit()
    return {"episode": episode_dict(ep)}


@router.post("/episodes/{eid}/shots")
async def build_shots(eid: int, db: Session = Depends(get_db)):
    """确认后的简介 → 分镜(job)。"""
    episode, series = _get_episode(db, eid)
    _current(episode)
    if not (episode.synopsis_ok and (episode.synopsis or "").strip()):
        raise HTTPException(status_code=400, detail="先确认简介(聊天拍板或选梗纲),再展开分镜。")
    kind = f"anime-shots-{eid}"
    for jid, job in list_running("anime-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    _idle(eid)

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            ep = session.get(AnimeEpisode, eid)
            if ep is None:
                raise ValueError("这一集已被删除,任务取消。")
            sr = session.get(AnimeSeries, ep.series_id)
            return await gen_shots(session, sr, ep, progress)

    return {"job_id": spawn_job(kind, work)}


@router.put("/episodes/{eid}/shots")
def put_shots(eid: int, body: ShotsIn, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    _idle(eid)
    _current(row)
    try:
        row.shots = save_shots(row, body.shots)
        if row.script and not row.script.get("stale"):
            validate_script_shots(row.shots, row.script, episode_cast(_series, row))
    except AnimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"episode": episode_dict(row)}


# ---- 端点:整集分段提示词 ---------------------------------------------

@router.post("/episodes/{eid}/film-prompt")
async def build_film_prompt_route(eid: int, body: FilmPromptGenIn | None = None, db: Session = Depends(get_db)):
    """分镜 → 整集分段精准提示词(job):每段 ≤ 单段上限,逐段复制贴外部模型。"""
    episode, series = _get_episode(db, eid)
    _current(episode)
    if not (episode.shots or []):
        raise HTTPException(status_code=400, detail="先展开分镜,再来出整集提示词。")
    segment_s = (body.segment_s if body else 15) or 15
    if segment_s not in (15, 30):
        raise HTTPException(status_code=400, detail="单段时长只支持 15 / 30 秒。")
    kind = f"anime-fp-{eid}"
    for jid, job in list_running("anime-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    _idle(eid)

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            ep = session.get(AnimeEpisode, eid)
            if ep is None:
                raise ValueError("这一集已被删除,任务取消。")
            sr = session.get(AnimeSeries, ep.series_id)
            return await build_film_prompt(session, sr, ep, progress, segment_s=segment_s)

    return {"job_id": spawn_job(kind, work)}


@router.get("/episodes/{eid}/film-prompt")
def get_film_prompt(eid: int, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    return {"film_prompt": row.film_prompt or ""}


@router.put("/episodes/{eid}/film-prompt")
def put_film_prompt(eid: int, body: FilmPromptIn, db: Session = Depends(get_db)):
    row, _series = _get_episode(db, eid)
    row.film_prompt = (body.film_prompt or "").strip()
    db.commit()
    return {"film_prompt": row.film_prompt}
