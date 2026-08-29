# app/api/drama/_common.py
# -*- coding: utf-8 -*-
"""漫剧工坊 API 共享模块:请求模型、工具函数、公共 imports。

拆分自原 app/api/drama.py(42KB 单文件)。各子路由模块从这里导入共享部分,
避免循环依赖。主路由在 __init__.py 聚合。
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
    build_episode_film_prompt,
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
    shot_refs_by_seq,
    shots_payload,
    style_card,
    style_card_dict,
)
from app.engines.drama.gender import VALID_GENDERS
from app.engines.drama.video import CLIP_LIMIT_DEFAULT, clips_payload
from app.engines.media.directions import DIRECTIONS
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.drama")

# 主路由前缀:所有子路由都挂在这个前缀下
ROUTER_PREFIX = "/api/projects/{project_id}/drama"


def make_sub_router() -> APIRouter:
    """创建子路由:不加前缀(由主 router 统一管理),只继承鉴权依赖。

    每个子模块(style/characters/episodes/...)调用这个函数创建自己的 router,
    然后在 __init__.py 里用 router.include_router 聚合。路径前缀和鉴权
    只在主 router 定义一处,include_router 会自动继承,避免路径重复拼接。
    """
    return APIRouter(
        tags=["drama"],
        dependencies=[Depends(get_current_user)],
    )


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


class FilmPromptIn(BaseModel):
    """整片提示词手动保存:整段替换(粘贴自己写的版本也走这里)。"""

    film_prompt: str = ""


class FilmPromptGenIn(BaseModel):
    """整片提示词生成参数:单段时长上限(外部模型单次生成的现实上限)。"""

    segment_s: int = Field(default=15)


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
    # 配音情绪(完整档对白链;空=平静,白名单校验在 patch_shot 里做)
    emotion: str | None = None
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


# =============== 工具函数 ===============


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
