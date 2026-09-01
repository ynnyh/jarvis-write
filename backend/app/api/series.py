# app/api/series.py
# -*- coding: utf-8 -*-
"""角色系列短片接口:固定主角的 5-15 秒系列短视频,主角档案持久化、剧情按集喂入。

GET    /api/series/meta                            方向目录/时长范围/字数上限
GET    /api/series/characters                      我的角色列表
POST   /api/series/characters                      新建角色 {name, look, direction, default_duration_s, style_hints}
POST   /api/series/characters/draft-look           AI 代写定妆草稿 {brief, direction, style_hints}(不落库)
GET    /api/series/characters/{cid}                角色详情(含剧集列表)
PATCH  /api/series/characters/{cid}                改档案
DELETE /api/series/characters/{cid}                删角色(级联删剧集+清参考图;生成中 409)
POST   /api/series/characters/{cid}/episodes       新建一集 {plot, duration_s}
POST   /api/series/episodes/{eid}/generate         生成成片提示词(async)
PUT    /api/series/episodes/{eid}                  手改剧情/输出/时长
DELETE /api/series/episodes/{eid}                  删一集(生成中 409)
POST   /api/series/characters/{cid}/reference      定妆参考图上传(multipart)
POST   /api/series/characters/{cid}/reference/link 定妆参考图外链
DELETE /api/series/characters/{cid}/reference/{j}  删参考图(上传连文件删)
GET    /api/series/characters/{cid}/reference/{j}  读参考图(鉴权)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import storage
from app.auth import assert_project_owner, get_current_user
from app.db.models import SeriesCharacter, SeriesEpisode
from app.db.session import get_db
from app.engines.media.directions import VALID_DIRECTIONS
from app.engines.media.text import clip
from app.engines.series import (
    BRIEF_MAX,
    HINTS_MAX,
    LOOK_MAX,
    MAX_DURATION_S,
    MIN_DURATION_S,
    NAME_MAX,
    PLOT_MAX,
    SeriesError,
    character_dict,
    draft_look,
    episode_dict,
    generate_episode,
    norm_output,
)
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.series")

router = APIRouter(prefix="/api/series", tags=["series"], dependencies=[Depends(get_current_user)])


# ---- 入参模型 ---------------------------------------------

class CharacterCreateIn(BaseModel):
    name: str = Field(max_length=NAME_MAX)
    look: str = ""
    direction: str = "render3d"
    default_duration_s: int = Field(default=10)
    style_hints: str = ""


class CharacterPatchIn(BaseModel):
    name: str | None = None
    look: str | None = None
    direction: str | None = None
    default_duration_s: int | None = None
    style_hints: str | None = None


class DraftLookIn(BaseModel):
    """AI 代写定妆草稿:不落库,返回草稿由用户确认后再保存。"""
    brief: str = Field(max_length=BRIEF_MAX)
    direction: str = "render3d"
    style_hints: str = ""


class EpisodeCreateIn(BaseModel):
    plot: str = Field(max_length=PLOT_MAX)
    duration_s: int | None = None  # 缺省用角色默认时长


class EpisodePatchIn(BaseModel):
    plot: str | None = None
    duration_s: int | None = None
    output: dict | None = None  # 手改输出(整段替换)


class RefLinkIn(BaseModel):
    """贴一张定妆参考图外链(生图站的图片地址,可能带时效签名)。"""
    url: str = ""
    note: str = ""


# ---- 校验/取行 ---------------------------------------------

def _check_direction(direction: str) -> str:
    # 系列角色不走「AI 按书定」(auto 是书的语境),必须选一个具体方向
    if direction not in VALID_DIRECTIONS or direction == "auto":
        raise HTTPException(status_code=400, detail="选一个具体画风方向(不支持「AI 按书定」)。")
    return direction


def _check_duration(duration_s: int) -> int:
    if not MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
        raise HTTPException(status_code=400, detail=f"时长只支持 {MIN_DURATION_S}-{MAX_DURATION_S} 秒。")
    return duration_s


def _get_character(db: Session, cid: int) -> SeriesCharacter:
    row = db.get(SeriesCharacter, cid)
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    assert_project_owner(row)
    return row


def _get_episode(db: Session, eid: int) -> tuple[SeriesEpisode, SeriesCharacter]:
    row = db.get(SeriesEpisode, eid)
    if row is None:
        raise HTTPException(status_code=404, detail="这一集不存在")
    assert_project_owner(row)
    character = _get_character(db, row.character_id)
    return row, character


def _episode_generating(eid: int) -> bool:
    """这集是否有生成任务在跑(删改前查;kind 见 generate 端点)。"""
    return any(job["kind"] == f"series-gen-{eid}" for _jid, job in list_running("series-"))


def _clean_refs(refs) -> list[dict]:
    """收敛 ref_images:只留合法形态(kind∈upload/url,src 非空)。"""
    out: list[dict] = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        kind = r.get("kind")
        src = str(r.get("src") or "")
        if kind not in ("upload", "url") or not src:
            continue
        out.append({"kind": kind, "src": src, "note": clip(r.get("note"), 100)})
    return out


# ---- meta / 角色 CRUD ---------------------------------------------

@router.get("/meta")
async def series_meta():
    from app.engines.media.directions import DIRECTIONS

    return {
        # 系列角色必须选具体方向,目录里剔掉 auto
        "directions": [
            {"key": d["key"], "label": d["label"], "tip": d["tip"]}
            for d in DIRECTIONS if d["key"] != "auto"
        ],
        "min_duration_s": MIN_DURATION_S,
        "max_duration_s": MAX_DURATION_S,
        "name_max": NAME_MAX,
        "brief_max": BRIEF_MAX,
        "look_max": LOOK_MAX,
        "plot_max": PLOT_MAX,
        "hints_max": HINTS_MAX,
    }


@router.get("/characters")
async def list_characters(db: Session = Depends(get_db)):
    from app.auth import current_user_id

    rows = (
        db.query(SeriesCharacter)
        .filter(SeriesCharacter.user_id == current_user_id.get())
        .order_by(SeriesCharacter.updated_at.desc())
        .limit(100)
        .all()
    )
    return {"characters": [character_dict(r) for r in rows]}


@router.post("/characters")
async def create_character(body: CharacterCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    if not body.name.strip():
        raise HTTPException(status_code=400, detail="先给主角起个名字(如「小浣熊」)。")
    if not body.look.strip():
        raise HTTPException(status_code=400, detail="定妆描述不能为空——写一句概念后点「AI 代写」,或直接手写。")
    row = SeriesCharacter(
        user_id=current_user_id.get(),
        name=clip(body.name, NAME_MAX),
        look=body.look.strip()[:LOOK_MAX],
        direction=_check_direction(body.direction),
        default_duration_s=_check_duration(body.default_duration_s),
        style_hints=clip(body.style_hints, HINTS_MAX * 2),
    )
    db.add(row)
    db.commit()
    return {"character_row": character_dict(row)}


@router.post("/characters/draft-look")
async def draft_look_api(body: DraftLookIn):
    """一句话概念 → 定妆草稿。同步短调用(单发 LLM,前端给长超时);不落库。"""
    if not body.brief.strip():
        raise HTTPException(status_code=400, detail="先写一句主角概念(如「一只戴红围巾、爱囤零食的小浣熊」)。")
    _check_direction(body.direction)
    try:
        look = await draft_look(body.brief.strip(), body.direction, clip(body.style_hints, HINTS_MAX * 2))
    except SeriesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"look": look}


@router.get("/characters/{cid}")
async def get_character(cid: int, db: Session = Depends(get_db)):
    row = _get_character(db, cid)
    episodes = (
        db.query(SeriesEpisode)
        .filter(SeriesEpisode.character_id == cid)
        .order_by(SeriesEpisode.id.desc())
        .limit(200)
        .all()
    )
    return {"character_row": character_dict(row), "episodes": [episode_dict(e) for e in episodes]}


@router.patch("/characters/{cid}")
async def patch_character(cid: int, body: CharacterPatchIn, db: Session = Depends(get_db)):
    row = _get_character(db, cid)
    if _episode_generating_for_character(db, cid):
        raise HTTPException(status_code=409, detail="这个角色有剧集正在生成,稍等再改档案。")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="角色名不能为空。")
        row.name = clip(body.name, NAME_MAX)
    if body.look is not None:
        if not body.look.strip():
            raise HTTPException(status_code=400, detail="定妆描述不能为空。")
        row.look = body.look.strip()[:LOOK_MAX]
    if body.direction is not None:
        row.direction = _check_direction(body.direction)
    if body.default_duration_s is not None:
        row.default_duration_s = _check_duration(body.default_duration_s)
    if body.style_hints is not None:
        row.style_hints = clip(body.style_hints, HINTS_MAX * 2)
    db.commit()
    return {"character_row": character_dict(row)}


def _episode_generating_for_character(db: Session, cid: int) -> bool:
    episodes = db.query(SeriesEpisode.id).filter(SeriesEpisode.character_id == cid).all()
    return any(_episode_generating(eid) for (eid,) in episodes)


@router.delete("/characters/{cid}")
async def delete_character(cid: int, db: Session = Depends(get_db)):
    row = _get_character(db, cid)
    if _episode_generating_for_character(db, cid):
        raise HTTPException(
            status_code=409,
            detail="这个角色有剧集正在生成,等它跑完再删除(刷新页面可看进度)。",
        )
    # 参考图按角色号独占 series/<cid>/ 目录,行走了文件不能留在卷里吃配额
    db.query(SeriesEpisode).filter(SeriesEpisode.character_id == cid).delete()
    db.delete(row)
    db.commit()
    storage.delete_series_dir(cid)
    return {"ok": True}


# ---- 剧集 ---------------------------------------------

@router.post("/characters/{cid}/episodes")
async def create_episode(cid: int, body: EpisodeCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    character = _get_character(db, cid)
    if not body.plot.strip():
        raise HTTPException(status_code=400, detail="先写这一集的剧情(一句话到一段话都行)。")
    row = SeriesEpisode(
        user_id=current_user_id.get(),
        character_id=character.id,
        plot=body.plot.strip()[:PLOT_MAX],
        duration_s=_check_duration(body.duration_s or character.default_duration_s),
    )
    db.add(row)
    db.commit()
    return {"episode_row": episode_dict(row)}


@router.post("/episodes/{eid}/generate")
async def generate_episode_api(eid: int, db: Session = Depends(get_db)):
    _row, character = _get_episode(db, eid)
    if not (character.look or "").strip():
        raise HTTPException(status_code=400, detail="这个角色还没有定妆描述,先补档案再生成。")
    kind = f"series-gen-{eid}"
    for jid, job in list_running("series-"):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            ep = session.get(SeriesEpisode, eid)
            if ep is None:
                raise ValueError("这一集已被删除,任务取消。")
            ch = session.get(SeriesCharacter, ep.character_id)
            ep.status = "generating"
            session.commit()
            try:
                return await generate_episode(session, ep, ch, progress)
            except Exception:
                # 失败回 draft:别让卡片永远卡在「生成中」(任务中心能看到失败原因)
                ep.status = "draft"
                session.commit()
                raise

    return {"job_id": spawn_job(kind, work)}


@router.put("/episodes/{eid}")
async def patch_episode(eid: int, body: EpisodePatchIn, db: Session = Depends(get_db)):
    row, _character = _get_episode(db, eid)
    if _episode_generating(eid):
        raise HTTPException(status_code=409, detail="这一集正在生成,等它跑完再改。")
    if body.plot is not None:
        if not body.plot.strip():
            raise HTTPException(status_code=400, detail="剧情不能为空。")
        row.plot = body.plot.strip()[:PLOT_MAX]
    if body.duration_s is not None:
        row.duration_s = _check_duration(body.duration_s)
    if body.output is not None:
        # 手改输出整段替换;title 空了用剧情首行兜底
        row.output = norm_output(body.output, fallback_title=row.plot)
        if row.output["prompt_cn"]:
            row.status = "done"
    db.commit()
    return {"episode_row": episode_dict(row)}


@router.delete("/episodes/{eid}")
async def delete_episode(eid: int, db: Session = Depends(get_db)):
    _get_episode(db, eid)
    if _episode_generating(eid):
        raise HTTPException(status_code=409, detail="这一集正在生成,等它跑完再删除。")
    db.query(SeriesEpisode).filter(SeriesEpisode.id == eid).delete()
    db.commit()
    return {"ok": True}


# ---- 定妆参考图 ---------------------------------------------

@router.post("/characters/{cid}/reference")
async def upload_reference(
    cid: int, file: UploadFile = File(...), note: str = Form(""),
    db: Session = Depends(get_db),
):
    """传一张定妆参考图(文生图出的定妆照,出片时丢给图生视频当人物锚)。"""
    row = _get_character(db, cid)
    if len(row.ref_images or []) >= storage.MAX_REFS_PER_CHARACTER:
        raise HTTPException(
            status_code=400,
            detail=f"每个角色最多 {storage.MAX_REFS_PER_CHARACTER} 张定妆参考图,先删掉一张再传。",
        )
    data = await file.read(storage.MAX_IMAGE_BYTES + 1)
    try:
        rel = storage.save_series_ref(cid, data, len(row.ref_images or []))
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    row.ref_images = list(row.ref_images or []) + [
        {"kind": "upload", "src": rel, "note": clip(note, 100)}
    ]
    flag_modified(row, "ref_images")
    db.commit()
    return {"character_row": character_dict(row)}


@router.post("/characters/{cid}/reference/link")
async def link_reference(cid: int, body: RefLinkIn, db: Session = Depends(get_db)):
    """贴一张定妆参考图外链(生图站地址;带时效签名会失效,前端要提示建议下载后上传)。"""
    row = _get_character(db, cid)
    if len(row.ref_images or []) >= storage.MAX_REFS_PER_CHARACTER:
        raise HTTPException(
            status_code=400,
            detail=f"每个角色最多 {storage.MAX_REFS_PER_CHARACTER} 张定妆参考图,先删掉一张再贴。",
        )
    url = clip(body.url, 500)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填 http/https 开头的图片地址。")
    row.ref_images = list(row.ref_images or []) + [
        {"kind": "url", "src": url, "note": clip(body.note, 100)}
    ]
    flag_modified(row, "ref_images")
    db.commit()
    return {"character_row": character_dict(row)}


@router.delete("/characters/{cid}/reference/{img_index}")
async def delete_reference(cid: int, img_index: int, db: Session = Depends(get_db)):
    row = _get_character(db, cid)
    refs = list(row.ref_images or [])
    if not 0 <= img_index < len(refs):
        raise HTTPException(status_code=404, detail="这张参考图不存在。")
    gone = refs.pop(img_index)
    row.ref_images = refs
    flag_modified(row, "ref_images")
    db.commit()
    if gone["kind"] == "upload":
        storage.delete(gone["src"])
    return {"character_row": character_dict(row)}


@router.get("/characters/{cid}/reference/{img_index}")
async def read_reference(cid: int, img_index: int, db: Session = Depends(get_db)):
    """读一张上传的定妆参考图(走鉴权;上传目录不挂静态服务,<img> 由前端转 blob 显示)。"""
    row = _get_character(db, cid)
    refs = list(row.ref_images or [])
    if not 0 <= img_index < len(refs) or refs[img_index]["kind"] != "upload":
        raise HTTPException(status_code=404, detail="这张参考图不存在。")
    rel = refs[img_index]["src"]
    try:
        path = storage.resolve(rel)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="参考图文件已丢失。")
    return Response(content=path.read_bytes(), media_type=storage.content_type_of(rel))
