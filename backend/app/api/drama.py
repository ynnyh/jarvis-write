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
PATCH  /api/projects/{id}/drama/characters/{cid}          编辑/锁定角色卡
GET    /api/projects/{id}/drama/episodes                  集列表
POST   /api/projects/{id}/drama/episodes/plan             集数规划(async,覆盖范围内旧集)
DELETE /api/projects/{id}/drama/episodes/{eid}            删一集(连分镜)
GET    /api/projects/{id}/drama/episodes/{eid}            集详情(含分镜)
POST   /api/projects/{id}/drama/episodes/{eid}/script     写剧本(async)
POST   /api/projects/{id}/drama/episodes/{eid}/storyboard 拆分镜(async,覆盖式)
POST   /api/projects/{id}/drama/episodes/{eid}/prompts    出三轨提示词(async)
POST   /api/projects/{id}/drama/voice-cast/generate          声线选型卡(async,阶段 2)
GET    /api/projects/{id}/drama/episodes/{eid}/pack          成片包(配音稿+剪辑清单)
POST   /api/projects/{id}/drama/episodes/{eid}/pack          生成成片包(async)
PATCH  /api/projects/{id}/drama/shots/{sid}               手动改分镜/提示词
GET    /api/projects/{id}/drama/episodes/{eid}/export     导出 ?format=md|csv|json|pack|srt
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaProductionPack,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
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
    generate_assets,
    generate_style_card,
    generate_voice_cast,
    plan_episodes,
    render_shot_prompts,
    write_episode_script,
)
from app.engines.drama.common import (
    MODE_DESC,
    VALID_MODES,
    approved_chapter_numbers,
    character_card_dict,
    clip,
    episode_dict,
    scene_card_dict,
    shot_dict,
    style_card_dict,
)
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


class CharacterCardIn(BaseModel):
    name: str | None = None
    appearance_cn: str | None = None
    appearance_en: str | None = None
    outfit_cn: str | None = None
    voice_desc: str | None = None
    tts_hint: str | None = None
    reading_notes: str | None = None
    locked: bool | None = None


class PlanIn(BaseModel):
    from_chapter: int = Field(ge=1)
    to_chapter: int = Field(ge=1)
    mode: str = "dialogue"
    duration_s: int = Field(default=90, ge=30, le=180)


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


# =============== 工具 ===============

def _get_episode(db: Session, project_id: int, episode_id: int) -> DramaEpisode:
    ep = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.id == episode_id, DramaEpisode.project_id == project_id)
        .first()
    )
    if ep is None:
        raise HTTPException(status_code=404, detail="这一集不存在。")
    return ep


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
    """准入门槛数据:已定稿章号(前端用它显示引导/章节范围选择)。"""
    get_project_or_404(db, project_id)
    approved = approved_chapter_numbers(db, project_id)
    return {
        "approved_chapters": approved,
        "approved_count": len(approved),
        "modes": [{"key": k, "label": v} for k, v in MODE_DESC.items()],
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
    card.style_name = clip(body.style_name, 60)
    card.style_cn = clip(body.style_cn, 400)
    card.style_en = clip(body.style_en, 400)
    card.negative = clip(body.negative, 300)
    card.ratio = clip(body.ratio, 10) or "9:16"
    db.commit()
    return {"style": style_card_dict(card)}


@router.post("/style/generate")
async def generate_style(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    if not project.topic.strip():
        raise HTTPException(status_code=400, detail="请先在「概念」确定本书主题,再定美术风格。")
    kind = f"drama-style-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_style_card(session, proj, progress)

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
        "cards": [character_card_dict(c) for c in cards],
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
    if body.name is not None:
        card.name = clip(body.name, 200)
    for field in ("appearance_cn", "appearance_en", "outfit_cn", "voice_desc",
                  "tts_hint", "reading_notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(card, field, value)
    if body.locked is not None:
        card.locked = body.locked
    db.commit()
    return {"card": character_card_dict(card)}


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
    return {"episode": episode_dict(ep), "shots": [shot_dict(s) for s in shots]}


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
    shot = (
        db.query(DramaShot)
        .join(DramaEpisode, DramaShot.episode_id == DramaEpisode.id)
        .filter(DramaShot.id == shot_id, DramaEpisode.project_id == project_id)
        .first()
    )
    if shot is None:
        raise HTTPException(status_code=404, detail="分镜不存在。")
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
    for field in ("prompt_cn", "prompt_en", "negative"):
        value = getattr(body, field)
        if value is not None:
            setattr(shot, field, value)
    db.commit()
    return {"shot": shot_dict(shot)}


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
        content = export_csv(ep, shots)
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
