# app/api/drama.py
# -*- coding: utf-8 -*-
"""漫剧工坊接口:风格卡/资产卡 → 集规划 → 剧本 → 分镜 → 三轨提示词 → 导出。

沿用 media.py 的「只产提示词」哲学与异步 job 套路(立即返回 job_id,前端轮询);
job 内部用独立 SessionLocal(请求级 session 在响应后关闭,后台任务不能复用,
同 chapters/generation.py 的处理)。准入门槛:规划/剧本要求有已定稿章节。

GET    /api/projects/{id}/drama/meta                      准入信息(已定稿章号列表)
GET    /api/projects/{id}/drama/style                     风格卡(可能 null)
PUT    /api/projects/{id}/drama/style                     手动保存风格卡
POST   /api/projects/{id}/drama/style/generate            生成风格卡(async)
GET    /api/projects/{id}/drama/characters                角色卡+场景卡列表
POST   /api/projects/{id}/drama/characters/generate       批量生成资产卡(async)
PATCH  /api/projects/{id}/drama/characters/{cid}          编辑/锁定角色卡(含性别)
POST   /api/projects/{id}/drama/characters/{cid}/regenerate 只重出这一张角色卡(async)
POST   /api/projects/{id}/drama/characters/ref-prompts    出定妆照提示词(async)
POST   /api/projects/{id}/drama/characters/{cid}/reference        上传定妆照(multipart)
POST   /api/projects/{id}/drama/characters/{cid}/reference/link   贴定妆照外链
DELETE /api/projects/{id}/drama/characters/{cid}/reference/{i}    删一张定妆照
GET    /api/projects/{id}/drama/characters/{cid}/reference/{i}    读定妆照(鉴权)
GET    /api/projects/{id}/drama/episodes                  集列表
POST   /api/projects/{id}/drama/episodes/plan             集数规划(async,覆盖范围内旧集)
DELETE /api/projects/{id}/drama/episodes/{eid}            删一集(连分镜)
GET    /api/projects/{id}/drama/episodes/{eid}            集详情(含分镜)
GET    /api/projects/{id}/drama/episodes/{eid}/clips      视频段计划 ?limit_s=10(确定性,不调 LLM)
POST   /api/projects/{id}/drama/episodes/{eid}/script     写剧本(async)
POST   /api/projects/{id}/drama/episodes/{eid}/storyboard 拆分镜(async,覆盖式)
POST   /api/projects/{id}/drama/episodes/{eid}/prompts    出三轨提示词(async)
POST   /api/projects/{id}/drama/voice-cast/generate          声线选型卡(async,阶段 2)
GET    /api/projects/{id}/drama/episodes/{eid}/pack          成片包(配音稿+剪辑清单)
POST   /api/projects/{id}/drama/episodes/{eid}/pack          生成成片包(async)
PATCH  /api/projects/{id}/drama/shots/{sid}               手动改分镜/提示词/运动轨/打勾
POST   /api/projects/{id}/drama/shots/{sid}/prompt        只重出这一格提示词(async)
POST   /api/projects/{id}/drama/shots/{sid}/asset         挂这一格的静帧(multipart)
POST   /api/projects/{id}/drama/shots/{sid}/asset/link    贴这一格静帧的外链
DELETE /api/projects/{id}/drama/shots/{sid}/asset/{i}     删一张静帧
GET    /api/projects/{id}/drama/shots/{sid}/asset/{i}     读一张静帧(鉴权)
GET    /api/projects/{id}/drama/episodes/{eid}/export     导出 ?format=md|csv|json|pack|srt
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import storage
from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaProductionPack,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    DramaTrailer,
    Project,
)
from app.db.session import get_db
from app.engines.drama import (
    build_production_pack,
    build_storyboard,
    export_csv,
    export_json,
    export_markdown,
    export_pack_markdown,
    export_srt,
    export_trailer_markdown,
    export_trailer_srt,
    generate_assets,
    generate_ref_sheets,
    generate_style_card,
    generate_trailer,
    generate_voice_cast,
    plan_episodes,
    recommend_directions,
    regenerate_character_card,
    render_shot_prompts,
    render_single_shot_prompt,
    write_episode_script,
)
from app.engines.drama.common import (
    MODE_DESC,
    VALID_DIRECTIONS,
    VALID_MODES,
    approved_chapter_numbers,
    character_card_dict,
    clip,
    episode_dict,
    ref_image_list,
    scene_card_dict,
    shot_asset_list,
    shot_progress,
    shots_payload,
    style_card,
    style_card_dict,
)
from app.engines.drama.gender import VALID_GENDERS
from app.engines.drama.video import CLIP_LIMIT_DEFAULT, clips_payload
from app.engines.media.directions import DIRECTIONS
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.drama")

router = APIRouter(prefix="/api/projects/{project_id}/drama", tags=["drama"],
                   dependencies=[Depends(get_current_user)])


# =============== 请求模型 ===============

class StyleCardIn(BaseModel):
    style_name: str = ""
    style_cn: str = ""
    style_en: str = ""
    negative: str = ""
    ratio: str = "9:16"
    direction: str | None = None


class StyleGenIn(BaseModel):
    direction: str = "auto"


class CharacterCardIn(BaseModel):
    name: str | None = None
    # 性别:""(未定)/female/male/other,别的写法一律 400 打回(见 drama/gender.py)
    gender: str | None = None
    appearance_cn: str | None = None
    appearance_en: str | None = None
    outfit_cn: str | None = None
    voice_desc: str | None = None
    tts_hint: str | None = None
    reading_notes: str | None = None
    ref_prompt_cn: str | None = None
    ref_prompt_en: str | None = None
    locked: bool | None = None


class RefPromptIn(BaseModel):
    """出定妆照提示词:names 空 = 只补缺;给了名字 = 强制重出那几张。"""

    names: list[str] = []


class RefLinkIn(BaseModel):
    url: str = ""
    note: str = ""


class PlanIn(BaseModel):
    from_chapter: int = Field(ge=1)
    to_chapter: int = Field(ge=1)
    mode: str = "dialogue"
    duration_s: int = Field(default=90, ge=30, le=180)


class TrailerIn(BaseModel):
    from_ep: int = Field(default=1, ge=1)
    to_ep: int = Field(default=9999, ge=1)
    target_s: int = Field(default=45, ge=20, le=90)


class ShotIn(BaseModel):
    scene_name: str | None = None
    characters: list[str] | None = None
    action_desc: str | None = None
    shot_type: str | None = None
    camera: str | None = None
    dialogue: str | None = None
    duration_s: int | None = Field(default=None, ge=1, le=10)
    prompt_cn: str | None = None
    prompt_en: str | None = None
    negative: str | None = None
    # 运动轨(图生视频用):手改这两栏比重出整格快,尤其「幅度太大」这种小毛病
    motion_cn: str | None = None
    motion_en: str | None = None
    # 施工进度:成片在哪(外链/本地文件名)+ 出图、生视频各自做完没有
    clip_ref: str | None = None
    done_still: bool | None = None
    done_video: bool | None = None


class ShotPromptIn(BaseModel):
    """单格重出提示词:note 是用户对这一格的额外要求(可空)。"""

    note: str = ""


# =============== 工具 ===============

def _require_owner(db: Session, project_id: int) -> None:
    """归属闸门:项目不存在或不属于当前用户 → 404(不泄露存在性)。

    放在三个「取子资源」的助手里(集/分镜/角色卡),而不是各路由自己记着调——
    子资源的路由多达十来条,漏一条就是别人能读、能删你的剧集。
    """
    get_project_or_404(db, project_id)


def _get_episode(db: Session, project_id: int, episode_id: int) -> DramaEpisode:
    _require_owner(db, project_id)
    ep = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.id == episode_id, DramaEpisode.project_id == project_id)
        .first()
    )
    if ep is None:
        raise HTTPException(status_code=404, detail="这一集不存在。")
    return ep


def _get_shot(db: Session, project_id: int, shot_id: int) -> DramaShot:
    _require_owner(db, project_id)
    shot = (
        db.query(DramaShot)
        .join(DramaEpisode, DramaShot.episode_id == DramaEpisode.id)
        .filter(DramaShot.id == shot_id, DramaEpisode.project_id == project_id)
        .first()
    )
    if shot is None:
        raise HTTPException(status_code=404, detail="分镜不存在。")
    return shot


def _get_character(db: Session, project_id: int, card_id: int) -> DramaCharacterCard:
    _require_owner(db, project_id)
    card = (
        db.query(DramaCharacterCard)
        .filter(
            DramaCharacterCard.id == card_id,
            DramaCharacterCard.project_id == project_id,
        )
        .first()
    )
    if card is None:
        raise HTTPException(status_code=404, detail="角色卡不存在。")
    return card


def _existing_job(prefix: str, kind: str) -> dict | None:
    """同 kind 任务已在跑 → 复用 job_id(防重复提交)。"""
    for jid, job in list_running(prefix):
        if job["kind"] == kind:
            return {"job_id": jid}
    return None


def _require_approved(db: Session, project_id: int, action: str) -> None:
    if not approved_chapter_numbers(db, project_id):
        raise HTTPException(
            status_code=400,
            detail=f"还没有已定稿章节,{action}要先用小说成稿当原料。先去写作区定稿几章。",
        )


# =============== 准入信息 ===============

@router.get("/meta")
async def drama_meta(project_id: int, db: Session = Depends(get_db)):
    """准入门槛数据:已定稿章号(前端用它显示引导/章节范围选择)+ 画风方向目录。"""
    get_project_or_404(db, project_id)
    approved = approved_chapter_numbers(db, project_id)
    return {
        "approved_chapters": approved,
        "approved_count": len(approved),
        "modes": [{"key": k, "label": v} for k, v in MODE_DESC.items()],
        "directions": DIRECTIONS,
    }


# =============== 美术风格卡 ===============

@router.get("/style")
async def get_style(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    card = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    return {"style": style_card_dict(card)}


@router.put("/style")
async def save_style(project_id: int, body: StyleCardIn, db: Session = Depends(get_db)):
    """手动保存风格卡(没有则建)。"""
    get_project_or_404(db, project_id)
    card = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    if card is None:
        card = DramaStyleCard(project_id=project_id)
        db.add(card)
    if body.direction is not None:
        if body.direction not in VALID_DIRECTIONS:
            raise HTTPException(status_code=400, detail=f"未知画风方向:{body.direction}")
        card.direction = body.direction
    card.style_name = clip(body.style_name, 60)
    card.style_cn = clip(body.style_cn, 400)
    card.style_en = clip(body.style_en, 400)
    card.negative = clip(body.negative, 300)
    card.ratio = clip(body.ratio, 10) or "9:16"
    db.commit()
    return {"style": style_card_dict(card)}


@router.post("/style/generate")
async def generate_style(
    project_id: int, body: StyleGenIn | None = None, db: Session = Depends(get_db)
):
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再定美术风格。")
    direction = (body.direction if body else "") or "auto"
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"未知画风方向:{direction}")
    kind = f"drama-style-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_style_card(session, proj, direction, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/style/recommend-directions")
async def recommend_directions_ep(project_id: int, db: Session = Depends(get_db)):
    """按书的题材/基调推荐前 3 个画风方向(带理由,按优先级排序);AI 荐,用户选。"""
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再推荐方向。")
    kind = f"drama-dirrec-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await recommend_directions(session, proj, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 角色卡 / 场景卡 ===============

@router.get("/characters")
async def list_characters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .order_by(DramaCharacterCard.id)
        .all()
    )
    scenes = (
        db.query(DramaSceneCard)
        .filter(DramaSceneCard.project_id == project_id)
        .order_by(DramaSceneCard.id)
        .all()
    )
    return {
        "cards": [character_card_dict(c, style_card(db, project_id)) for c in cards],
        "scenes": [scene_card_dict(s) for s in scenes],
    }


@router.post("/characters/generate")
async def generate_characters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    kind = f"drama-chars-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_assets(session, proj, progress)

    return {"job_id": spawn_job(kind, work)}


@router.patch("/characters/{card_id}")
async def patch_character(
    project_id: int, card_id: int, body: CharacterCardIn, db: Session = Depends(get_db)
):
    card = _get_character(db, project_id, card_id)
    if body.name is not None:
        card.name = clip(body.name, 200)
    if body.gender is not None:
        gender = clip(body.gender, 10)
        if gender and gender not in VALID_GENDERS:
            raise HTTPException(
                status_code=400,
                detail="性别只能是 female / male / other,或留空表示未定。",
            )
        card.gender = gender
    for field in ("appearance_cn", "appearance_en", "outfit_cn", "voice_desc",
                  "tts_hint", "reading_notes", "ref_prompt_cn", "ref_prompt_en"):
        value = getattr(body, field)
        if value is not None:
            setattr(card, field, value)
    if body.locked is not None:
        card.locked = body.locked
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


@router.post("/characters/{card_id}/regenerate")
async def regenerate_character(
    project_id: int, card_id: int, db: Session = Depends(get_db)
):
    """只重出这一张角色卡(async)。显式重出 = 覆盖,连锁定的卡也覆盖。

    典型用法:发现某个女角色被写成了男的 → 在卡上把性别改成「女」→ 点这个
    → AI 按拍板的性别重写外貌/服饰/声线三段(定妆照提示词另有「重出提示词」)。
    """
    get_project_or_404(db, project_id)
    _get_character(db, project_id, card_id)  # 归属校验:别人的卡 404
    kind = f"drama-charcard-{project_id}-{card_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await regenerate_character_card(session, proj, card_id, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 定妆照(角色参考图:人物一致性从文字层落到像素层) ===============

@router.post("/characters/ref-prompts")
async def generate_ref_prompts(
    project_id: int, body: RefPromptIn, db: Session = Depends(get_db)
):
    """出「定妆照」提示词(async)。names 为空 = 只补还没有的;给了名字 = 强制重出那几张。"""
    get_project_or_404(db, project_id)
    names = [clip(n, 200) for n in (body.names or []) if clip(n, 200)][:8]
    # kind 带上名字集合:重出 A 和重出 B 是两个任务,不该互相复用 job_id
    kind = f"drama-refsheet-{project_id}-{'+'.join(sorted(names)) or 'all'}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_ref_sheets(session, proj, names, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/characters/{card_id}/reference")
async def upload_character_reference(
    project_id: int,
    card_id: int,
    file: UploadFile = File(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """上传一张定妆照(本地文件)。类型按文件头判定,文件名由服务端生成。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    images = ref_image_list(card)
    if len(images) >= storage.MAX_REFS_PER_CARD:
        raise HTTPException(
            status_code=400,
            detail=f"每个角色最多 {storage.MAX_REFS_PER_CARD} 张定妆照,先删掉一张再传。",
        )
    data = await file.read(storage.MAX_IMAGE_BYTES + 1)
    try:
        rel = storage.save_character_ref(project_id, card_id, data, len(images))
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    card.ref_images = images + [
        {"kind": "upload", "src": rel, "note": clip(note, 100)}
    ]
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


@router.post("/characters/{card_id}/reference/link")
async def link_character_reference(
    project_id: int, card_id: int, body: RefLinkIn, db: Session = Depends(get_db)
):
    """贴一张定妆照外链(生图站的图片地址)。

    只收 http(s) 直链;平台链接普遍带时效签名,会失效,所以前端要提示「建议下载后上传」。
    """
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    images = ref_image_list(card)
    if len(images) >= storage.MAX_REFS_PER_CARD:
        raise HTTPException(
            status_code=400,
            detail=f"每个角色最多 {storage.MAX_REFS_PER_CARD} 张定妆照,先删掉一张再传。",
        )
    url = clip(body.url, 500)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填 http/https 开头的图片地址。")
    card.ref_images = images + [
        {"kind": "url", "src": url, "note": clip(body.note, 100)}
    ]
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


@router.delete("/characters/{card_id}/reference/{index}")
async def delete_character_reference(
    project_id: int, card_id: int, index: int, db: Session = Depends(get_db)
):
    """删一张定妆照(上传的连文件一起删)。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    images = ref_image_list(card)
    if not 0 <= index < len(images):
        raise HTTPException(status_code=404, detail="这张定妆照不存在。")
    gone = images.pop(index)
    if gone["kind"] == "upload":
        storage.delete(gone["src"])
    card.ref_images = images
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


@router.get("/characters/{card_id}/reference/{index}")
async def read_character_reference(
    project_id: int, card_id: int, index: int, db: Session = Depends(get_db)
):
    """读一张上传的定妆照(走鉴权,上传目录不挂静态服务)。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    images = ref_image_list(card)
    if not 0 <= index < len(images) or images[index]["kind"] != "upload":
        raise HTTPException(status_code=404, detail="这张定妆照不存在。")
    rel = images[index]["src"]
    try:
        path = storage.resolve(rel)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件已丢失,请重新上传。")
    return Response(
        content=path.read_bytes(),
        media_type=storage.content_type_of(rel),
        # 私有资产:允许浏览器本地缓存,但不许中间层/CDN 缓存
        headers={"Cache-Control": "private, max-age=86400"},
    )


# =============== 声线选型卡(阶段 2) ===============

@router.post("/voice-cast/generate")
async def generate_voice_cast_ep(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    kind = f"drama-voice-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_voice_cast(session, proj, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 集:规划 / 列表 / 详情 / 删除 ===============

@router.get("/episodes")
async def list_episodes(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    eps = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.project_id == project_id)
        .order_by(DramaEpisode.ep_index)
        .all()
    )
    return {"episodes": [episode_dict(e) for e in eps]}


@router.post("/episodes/plan")
async def plan(project_id: int, body: PlanIn, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    _require_approved(db, project_id, "漫剧改编")
    if body.to_chapter < body.from_chapter:
        raise HTTPException(status_code=400, detail="结束章号不能小于起始章号。")
    if body.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode 必须是 {'/'.join(VALID_MODES)}。")
    kind = f"drama-plan-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    from_ch, to_ch, mode, duration = (
        body.from_chapter, body.to_chapter, body.mode, body.duration_s,
    )

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await plan_episodes(
                session, proj, from_ch, to_ch, mode, duration, progress
            )

    return {"job_id": spawn_job(kind, work)}


@router.get("/episodes/{episode_id}")
async def get_episode(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    ep = _get_episode(db, project_id, episode_id)
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == ep.id)
        .order_by(DramaShot.seq)
        .all()
    )
    return {
        "episode": episode_dict(ep),
        "shots": shots_payload(db, project_id, shots),
        # 施工进度(静帧/视频各做完几格):一集几十格,进度得能一眼看见
        "progress": shot_progress(shots),
    }


@router.get("/episodes/{episode_id}/clips")
async def get_episode_clips(
    project_id: int,
    episode_id: int,
    limit_s: int = CLIP_LIMIT_DEFAULT,
    db: Session = Depends(get_db),
):
    """视频段计划:把分镜格并成「一次生成一段」(不超过站点单次时长上限)。

    视频站单次只能出 5-15 秒,而分镜格是 2-8 秒——实际做法是一次生成一段、
    再在画布/时间线上拼。这一步全确定性(不调 LLM,改上限即时重算)。
    """
    ep = _get_episode(db, project_id, episode_id)
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == ep.id)
        .order_by(DramaShot.seq)
        .all()
    )
    if not shots:
        raise HTTPException(status_code=400, detail="这一集还没有分镜,先「拆分镜」。")
    style = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    return {"plan": clips_payload(shots, style, limit_s)}


@router.delete("/episodes/{episode_id}")
async def delete_episode(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    ep = _get_episode(db, project_id, episode_id)
    db.query(DramaShot).filter(DramaShot.episode_id == ep.id).delete(
        synchronize_session=False
    )
    db.delete(ep)
    db.commit()
    # 删了中间集,后续集顺位补上(重排序号)
    remaining = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.project_id == project_id)
        .order_by(DramaEpisode.source_chapter, DramaEpisode.id)
        .all()
    )
    for i, row in enumerate(remaining, start=1):
        row.ep_index = i
    db.commit()
    return {"ok": True}


# =============== 集:剧本 / 分镜 / 提示词 ===============

def _episode_job(kind: str) -> dict | None:
    """集级任务去重:同任务在跑则复用 job_id。"""
    return _existing_job("drama-", kind)


def _load_for_job(session, project_id: int, episode_id: int):
    """job 内部重载项目与集(请求校验过后任务才排队,期间可能已被删)。"""
    proj = session.get(Project, project_id)
    ep = session.get(DramaEpisode, episode_id)
    if proj is None or ep is None:
        raise ValueError("项目或这一集已被删除,任务取消。")
    return proj, ep


@router.post("/episodes/{episode_id}/script")
async def write_script(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    _get_episode(db, project_id, episode_id)  # 归属校验
    kind = f"drama-script-{episode_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            return await write_episode_script(session, proj, ep, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/episodes/{episode_id}/storyboard")
async def storyboard(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    _get_episode(db, project_id, episode_id)
    kind = f"drama-board-{episode_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            return await build_storyboard(session, proj, ep, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/episodes/{episode_id}/prompts")
async def prompts(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    _get_episode(db, project_id, episode_id)
    kind = f"drama-prompts-{episode_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            return await render_shot_prompts(session, proj, ep, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 成片包(阶段 2:配音稿 + 剪辑清单) ===============

@router.post("/episodes/{episode_id}/pack")
async def build_pack(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    _get_episode(db, project_id, episode_id)
    kind = f"drama-pack-{episode_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            return await build_production_pack(session, proj, ep, progress)

    return {"job_id": spawn_job(kind, work)}


@router.get("/episodes/{episode_id}/pack")
async def get_pack(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    _get_episode(db, project_id, episode_id)
    row = (
        db.query(DramaProductionPack)
        .filter(DramaProductionPack.episode_id == episode_id)
        .first()
    )
    return {"pack": row.pack if row else None}


# =============== 分镜手动编辑 ===============

@router.patch("/shots/{shot_id}")
async def patch_shot(project_id: int, shot_id: int, body: ShotIn, db: Session = Depends(get_db)):
    shot = _get_shot(db, project_id, shot_id)
    if body.scene_name is not None:
        shot.scene_name = clip(body.scene_name, 200)
    if body.characters is not None:
        shot.characters = [clip(c, 200) for c in body.characters][:6]
    if body.action_desc is not None:
        shot.action_desc = body.action_desc
    if body.shot_type is not None:
        shot.shot_type = clip(body.shot_type, 20)
    if body.camera is not None:
        shot.camera = clip(body.camera, 20)
    if body.dialogue is not None:
        shot.dialogue = body.dialogue
    if body.duration_s is not None:
        shot.duration_s = body.duration_s
    for field in ("prompt_cn", "prompt_en", "negative", "motion_cn", "motion_en"):
        value = getattr(body, field)
        if value is not None:
            setattr(shot, field, value)
    if body.clip_ref is not None:
        shot.clip_ref = clip(body.clip_ref, 500)
    # 打勾栏:两个方向都要收(取消打勾也是正常操作),所以只判 None
    if body.done_still is not None:
        shot.done_still = bool(body.done_still)
    if body.done_video is not None:
        shot.done_video = bool(body.done_video)
    db.commit()
    return {"shot": shots_payload(db, project_id, [shot])[0]}


@router.post("/shots/{shot_id}/prompt")
async def regen_shot_prompt(
    project_id: int,
    shot_id: int,
    body: ShotPromptIn | None = None,
    db: Session = Depends(get_db),
):
    """只重出这一格的三轨提示词(其余格不动),note 是这一格的额外要求。

    整集重跑几十格又慢又会覆盖已手改的格子,而「就这一格不满意」是最高频的场景。
    """
    shot = _get_shot(db, project_id, shot_id)
    episode_id = shot.episode_id
    note = clip(body.note if body else "", 300)
    kind = f"drama-shot-{shot_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            row = session.get(DramaShot, shot_id)
            if row is None:
                raise ValueError("这一格已不存在(可能重拆过分镜),任务取消。")
            return await render_single_shot_prompt(session, proj, ep, row, note, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 逐格挂素材(出好的静帧挂回那一格)===============

@router.post("/shots/{shot_id}/asset")
async def upload_shot_asset(
    project_id: int,
    shot_id: int,
    file: UploadFile = File(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """把这一格出好的静帧挂回来(上传本地文件)。

    为什么要挂回来:一集几十格,在本站之外一格一格出图,做到哪儿全靠人脑记 =
    必然做丢或重做。挂上之后段计划里那一段的「首帧图已就位」会亮,也顺手打上勾。
    """
    shot = _get_shot(db, project_id, shot_id)
    assets = shot_asset_list(shot)
    if len(assets) >= storage.MAX_ASSETS_PER_SHOT:
        raise HTTPException(
            status_code=400,
            detail=f"一格最多挂 {storage.MAX_ASSETS_PER_SHOT} 张静帧,先删掉一张再传。",
        )
    data = await file.read(storage.MAX_IMAGE_BYTES + 1)
    try:
        rel = storage.save_shot_asset(project_id, shot_id, data, len(assets))
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    shot.assets = assets + [{"kind": "upload", "src": rel, "note": clip(note, 100)}]
    # 挂上第一张就等于这一格的图做完了,不用再手点一次勾(想撤销照样能取消勾)
    shot.done_still = True
    db.commit()
    return {"shot": shots_payload(db, project_id, [shot])[0]}


@router.post("/shots/{shot_id}/asset/link")
async def link_shot_asset(
    project_id: int, shot_id: int, body: RefLinkIn, db: Session = Depends(get_db)
):
    """贴这一格静帧的外链(生图站的图片地址)。

    平台链接普遍带时效签名会失效,所以前端提示「建议下载后上传」——但不拦着用。
    """
    shot = _get_shot(db, project_id, shot_id)
    assets = shot_asset_list(shot)
    if len(assets) >= storage.MAX_ASSETS_PER_SHOT:
        raise HTTPException(
            status_code=400,
            detail=f"一格最多挂 {storage.MAX_ASSETS_PER_SHOT} 张静帧,先删掉一张再传。",
        )
    url = clip(body.url, 500)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填 http/https 开头的图片地址。")
    shot.assets = assets + [{"kind": "url", "src": url, "note": clip(body.note, 100)}]
    shot.done_still = True
    db.commit()
    return {"shot": shots_payload(db, project_id, [shot])[0]}


@router.delete("/shots/{shot_id}/asset/{index}")
async def delete_shot_asset(
    project_id: int, shot_id: int, index: int, db: Session = Depends(get_db)
):
    """删掉这一格挂着的一张静帧(上传的连文件一起删)。

    删到一张不剩就把「出图做完」的勾取消掉——留着勾等于骗自己这一格做完了。
    """
    shot = _get_shot(db, project_id, shot_id)
    assets = shot_asset_list(shot)
    if not 0 <= index < len(assets):
        raise HTTPException(status_code=404, detail="这张静帧不存在。")
    gone = assets.pop(index)
    if gone["kind"] == "upload":
        storage.delete(gone["src"])
    shot.assets = assets
    if not assets:
        shot.done_still = False
    db.commit()
    return {"shot": shots_payload(db, project_id, [shot])[0]}


@router.get("/shots/{shot_id}/asset/{index}")
async def read_shot_asset(
    project_id: int, shot_id: int, index: int, db: Session = Depends(get_db)
):
    """读这一格上传的静帧(走鉴权,上传目录不挂静态服务)。"""
    shot = _get_shot(db, project_id, shot_id)
    assets = shot_asset_list(shot)
    if not 0 <= index < len(assets) or assets[index]["kind"] != "upload":
        raise HTTPException(status_code=404, detail="这张静帧不存在。")
    try:
        path = storage.resolve(assets[index]["src"])
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件已丢失,请重新上传。")
    return Response(
        content=path.read_bytes(),
        media_type=storage.content_type_of(assets[index]["src"]),
        # 私有资产:允许浏览器本地缓存,但不许中间层/CDN 缓存
        headers={"Cache-Control": "private, max-age=86400"},
    )


# =============== 预告片(项目级,一条,重建覆盖) ===============

@router.post("/trailer/generate")
async def generate_trailer_ep(
    project_id: int, body: TrailerIn, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
    kind = f"drama-trailer-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing
    from_ep, to_ep, target_s = body.from_ep, body.to_ep, body.target_s

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_trailer(session, proj, from_ep, to_ep, target_s, progress)

    return {"job_id": spawn_job(kind, work)}


@router.get("/trailer")
async def get_trailer(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    row = (
        db.query(DramaTrailer).filter(DramaTrailer.project_id == project_id).first()
    )
    if row is None:
        return {"trailer": None}
    shots = row.shots or []
    return {
        "trailer": {
            "target_s": row.target_s,
            "title": row.title,
            "lines": row.lines or [],
            "shots": shots,
            "totals": {
                "shots": len(shots),
                "duration_s": sum(int(s.get("duration_s") or 0) for s in shots),
            },
        }
    }


@router.get("/trailer/export")
async def export_trailer(
    project_id: int, format: str = "md", db: Session = Depends(get_db)
):
    """预告片导出:md(拍摄手册)/ srt(字幕)。"""
    project = get_project_or_404(db, project_id)
    row = (
        db.query(DramaTrailer).filter(DramaTrailer.project_id == project_id).first()
    )
    if row is None or not row.shots:
        raise HTTPException(status_code=400, detail="还没生成预告片。")
    trailer = {"target_s": row.target_s, "title": row.title, "lines": row.lines or [],
               "shots": row.shots or [], "totals": {}}
    if format == "srt":
        content = export_trailer_srt(row.shots or [])
        media = "application/x-subrip; charset=utf-8"
        name = f"{project.title}-预告片.srt"
    else:
        content = export_trailer_markdown(project, trailer)
        media = "text/markdown; charset=utf-8"
        name = f"{project.title}-预告片-拍摄手册.md"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# =============== 导出 ===============

@router.get("/episodes/{episode_id}/export")
async def export_episode(
    project_id: int, episode_id: int, format: str = "md", db: Session = Depends(get_db)
):
    """拍摄手册导出:md(人读)/ csv(分镜表)/ json(全量)。"""
    project = get_project_or_404(db, project_id)
    ep = _get_episode(db, project_id, episode_id)
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == ep.id)
        .order_by(DramaShot.seq)
        .all()
    )
    style = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .all()
    )
    scenes = (
        db.query(DramaSceneCard)
        .filter(DramaSceneCard.project_id == project_id)
        .all()
    )
    base = f"{project.title}-第{ep.ep_index}集"
    if format == "csv":
        content = export_csv(ep, shots, style, cards)
        media = "text/csv; charset=utf-8"
        name = f"{base}-分镜.csv"
    elif format == "json":
        content = export_json(project, ep, shots, style, cards, scenes)
        media = "application/json; charset=utf-8"
        name = f"{base}.json"
    elif format == "srt":
        content = export_srt(shots)
        media = "application/x-subrip; charset=utf-8"
        name = f"{base}-字幕.srt"
    elif format == "pack":
        row = (
            db.query(DramaProductionPack)
            .filter(DramaProductionPack.episode_id == ep.id)
            .first()
        )
        if row is None or not row.pack:
            raise HTTPException(
                status_code=400, detail="这一集还没生成成片包,先点「出成片包」。"
            )
        content = export_pack_markdown(project, ep, row.pack)
        media = "text/markdown; charset=utf-8"
        name = f"{base}-成片包.md"
    else:
        content = export_markdown(project, ep, shots, style, cards, scenes)
        media = "text/markdown; charset=utf-8"
        name = f"{base}-拍摄手册.md"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
