# app/api/clips.py
# -*- coding: utf-8 -*-
"""情绪短片接口:15/30 秒命题短视频,批产三本子三选一;双入口(通用/小说衍生投流)。

GET    /api/clips/meta                    主题/时长/方向目录
GET    /api/clips?project_id=             我的短片列表(可按源项目过滤)
POST   /api/clips                         新建(theme/custom_theme/duration_s/direction/inspiration/source_project_id)
GET    /api/clips/{id}                    详情(含候选与选中本子)
POST   /api/clips/{id}/generate           批产三个本子(async)
POST   /api/clips/{id}/pick               选定 {index}
DELETE /api/clips/{id}                    删除
GET    /api/clips/{id}/export             导出 ?format=md|srt|json
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, get_current_user
from app.db.models import MoodClip, Project
from app.db.session import get_db
from app.engines.clips import (
    CLIP_THEMES,
    VALID_DURATIONS,
    VALID_THEMES,
    ClipBatchError,
    clip_dict,
    export_json,
    export_markdown,
    export_srt,
    generate_batch,
    pick_clip,
)
from app.engines.clips.common import STATUS_CN
from app.engines.media.directions import VALID_DIRECTIONS
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.clips")

router = APIRouter(prefix="/api/clips", tags=["clips"], dependencies=[Depends(get_current_user)])


class ClipCreateIn(BaseModel):
    theme: str = ""
    custom_theme: str = ""
    duration_s: int = Field(default=15)
    direction: str = "live"
    inspiration: str = ""
    source_project_id: int | None = None


class ClipPatchIn(BaseModel):
    inspiration: str | None = None
    duration_s: int | None = None
    direction: str | None = None


class PickIn(BaseModel):
    index: int = Field(ge=0, le=2)


def _get_clip(db: Session, clip_id: int) -> MoodClip:
    row = db.get(MoodClip, clip_id)
    if row is None:
        raise HTTPException(status_code=404, detail="短片不存在")
    assert_project_owner(row)
    return row


def _validate_common(theme: str, custom_theme: str, duration_s: int, direction: str) -> None:
    if theme and theme not in VALID_THEMES:
        raise HTTPException(status_code=400, detail=f"未知主题:{theme}")
    if not theme and not custom_theme.strip():
        raise HTTPException(status_code=400, detail="选一个情绪主题,或填自定义主题。")
    if duration_s not in VALID_DURATIONS:
        raise HTTPException(status_code=400, detail="时长只支持 15/30 秒。")
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"未知画风方向:{direction}")


@router.get("/meta")
async def clips_meta():
    from app.engines.media.directions import DIRECTIONS

    return {
        "themes": CLIP_THEMES,
        "durations": list(VALID_DURATIONS),
        "directions": [
            {"key": d["key"], "label": d["label"], "tip": d["tip"]} for d in DIRECTIONS
        ],
        "status_cn": STATUS_CN,
    }


@router.get("")
async def list_clips(project_id: int | None = None, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    uid = current_user_id.get()
    q = db.query(MoodClip).filter(MoodClip.user_id == uid)
    if project_id is not None:
        q = q.filter(MoodClip.source_project_id == project_id)
    rows = q.order_by(MoodClip.updated_at.desc()).limit(100).all()
    return {"clips": [clip_dict(r, with_candidates=False) for r in rows]}


@router.post("")
async def create_clip(body: ClipCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    _validate_common(body.theme, body.custom_theme, body.duration_s, body.direction)
    if body.source_project_id is not None:
        project = db.get(Project, body.source_project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        assert_project_owner(project)
    row = MoodClip(
        user_id=current_user_id.get(),
        source_project_id=body.source_project_id,
        theme=body.theme,
        custom_theme=body.custom_theme.strip()[:120],
        duration_s=body.duration_s,
        direction=body.direction,
        inspiration=body.inspiration.strip()[:500],
    )
    db.add(row)
    db.commit()
    return {"clip_row": clip_dict(row)}


@router.get("/{clip_id}")
async def get_clip(clip_id: int, db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    return {"clip_row": clip_dict(row)}


@router.patch("/{clip_id}")
async def patch_clip(clip_id: int, body: ClipPatchIn, db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    if body.inspiration is not None:
        row.inspiration = body.inspiration.strip()[:500]
    if body.duration_s is not None:
        if body.duration_s not in VALID_DURATIONS:
            raise HTTPException(status_code=400, detail="时长只支持 15/30 秒。")
        row.duration_s = body.duration_s
    if body.direction is not None:
        if body.direction not in VALID_DIRECTIONS:
            raise HTTPException(status_code=400, detail=f"未知画风方向:{body.direction}")
        row.direction = body.direction
    db.commit()
    return {"clip_row": clip_dict(row)}


@router.post("/{clip_id}/generate")
async def generate_clip(clip_id: int, db: Session = Depends(get_db)):
    _get_clip(db, clip_id)
    kind = f"clips-gen-{clip_id}"
    for jid, job in list_running("clips-"):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            row = session.get(MoodClip, clip_id)
            if row is None:
                raise ValueError("短片已被删除,任务取消。")
            return await generate_batch(session, row, progress)

    return {"job_id": spawn_job(kind, work)}


@router.post("/{clip_id}/pick")
async def pick(clip_id: int, body: PickIn, db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    try:
        result = pick_clip(db, row, body.index)
    except ClipBatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"clip_row": result}


@router.delete("/{clip_id}")
async def delete_clip(clip_id: int, db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    # 生成中拒绝删除:任务收尾要 UPDATE 这一行,行没了会 StaleDataError,
    # 几分钟的批产白跑还报一条费解的错(线上实测 21 分钟后崩在收尾)。
    for _jid, job in list_running("clips-gen-"):
        if job["kind"] == f"clips-gen-{clip_id}":
            raise HTTPException(
                status_code=409,
                detail="这条短片正在生成中,等它跑完再删除(刷新页面可看进度)。",
            )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/{clip_id}/export")
async def export_clip(clip_id: int, format: str = "md", db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    if not (row.clip or {}).get("shots"):
        raise HTTPException(status_code=400, detail="还没选定本子,先「三选一」。")
    base = f"{row.custom_theme or '情绪短片'}-{row.id}"
    if format == "srt":
        content, media, name = export_srt(row), "application/x-subrip; charset=utf-8", f"{base}.srt"
    elif format == "json":
        content, media, name = export_json(row), "application/json; charset=utf-8", f"{base}.json"
    else:
        content, media, name = export_markdown(row), "text/markdown; charset=utf-8", f"{base}-手卡.md"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
