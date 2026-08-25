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
    ClipBatchError,
    clip_dict,
    export_json,
    export_markdown,
    export_srt,
    generate_batch,
    pick_clip,
    reexpand_batch,
)
from app.engines.clips.common import (
    CLIP_THEMES,
    CLIPS_PLAYS,
    DIALOGUE_STYLES,
    INTENSITIES,
    PACINGS,
    STATUS_CN,
    VALID_DIALOGUE_STYLES,
    VALID_DURATIONS,
    VALID_INTENSITIES,
    VALID_MODES,
    VALID_PACINGS,
    VALID_PLAYS,
    VALID_THEMES,
)
from app.engines.media.directions import VALID_DIRECTIONS
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.clips")

router = APIRouter(prefix="/api/clips", tags=["clips"], dependencies=[Depends(get_current_user)])


class ClipCreateIn(BaseModel):
    mode: str = "mood"
    theme: str = ""
    custom_theme: str = ""
    duration_s: int = Field(default=15)
    direction: str = "live"
    inspiration: str = ""
    # 导向维度(细化"方向"):全部默认 auto/空,存量行为零变化
    dialogue_style: str = "auto"
    pacing: str = "auto"
    intensity: str = "auto"
    style_hints: str = ""
    source_project_id: int | None = None


class ClipPatchIn(BaseModel):
    inspiration: str | None = None
    duration_s: int | None = None
    direction: str | None = None
    dialogue_style: str | None = None
    pacing: str | None = None
    intensity: str | None = None
    style_hints: str | None = None


class GenerateIn(BaseModel):
    """换一批时的用户意见(可选):连同上一批切入摘要进提示词,这批避开旧方向。"""
    feedback: str = Field(default="", max_length=200)


class ReexpandIn(BaseModel):
    """单条重拍:保切入与画风,只重展开分镜。"""
    index: int = Field(ge=0, le=2)
    feedback: str = Field(default="", max_length=200)


class PickIn(BaseModel):
    index: int = Field(ge=0, le=2)


class ClipCardIn(BaseModel):
    """手卡编辑保存:选定本子的完整卡(台词/分镜/三轨提示词/金句),服务端归一化重算切段。"""
    card: dict


def _get_clip(db: Session, clip_id: int) -> MoodClip:
    row = db.get(MoodClip, clip_id)
    if row is None:
        raise HTTPException(status_code=404, detail="短片不存在")
    assert_project_owner(row)
    return row


def _validate_common(theme: str, custom_theme: str, duration_s: int, direction: str, mode: str = "mood") -> None:
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"未知工坊类型:{mode}")
    valid = VALID_PLAYS if mode == "play" else VALID_THEMES
    kind = "玩法" if mode == "play" else "情绪主题"
    if theme and theme not in valid:
        raise HTTPException(status_code=400, detail=f"未知{kind}:{theme}")
    if not theme and not custom_theme.strip():
        raise HTTPException(status_code=400, detail=f"选一个{kind}或填自定义主题。")
    if duration_s not in VALID_DURATIONS:
        raise HTTPException(status_code=400, detail="时长只支持 15/30 秒。")
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"未知画风方向:{direction}")


def _norm_steering(
    dialogue_style: str | None, pacing: str | None,
    intensity: str | None, style_hints: str | None,
) -> dict:
    """导向字段校验 + 归一(None = 不改;返回可直接 setattr 的非空子集)。"""
    out: dict = {}
    checks = (
        ("dialogue_style", dialogue_style, VALID_DIALOGUE_STYLES, "台词风格"),
        ("pacing", pacing, VALID_PACINGS, "节奏"),
        ("intensity", intensity, VALID_INTENSITIES, "情绪浓度"),
    )
    for name, value, valid, label in checks:
        if value is None:
            continue
        if value not in valid:
            raise HTTPException(status_code=400, detail=f"未知{label}:{value}")
        out[name] = value
    if style_hints is not None:
        out["style_hints"] = style_hints.strip()[:80]
    return out


@router.get("/meta")
async def clips_meta():
    from app.engines.media.directions import DIRECTIONS

    return {
        "themes": CLIP_THEMES,
        "plays": CLIPS_PLAYS,
        "durations": list(VALID_DURATIONS),
        "directions": [
            {"key": d["key"], "label": d["label"], "tip": d["tip"]} for d in DIRECTIONS
        ],
        "dialogue_styles": DIALOGUE_STYLES,
        "pacings": PACINGS,
        "intensities": INTENSITIES,
        "status_cn": STATUS_CN,
    }


@router.get("")
async def list_clips(project_id: int | None = None, mode: str | None = None, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    uid = current_user_id.get()
    q = db.query(MoodClip).filter(MoodClip.user_id == uid)
    if mode is not None:
        q = q.filter(MoodClip.mode == mode)
    if project_id is not None:
        q = q.filter(MoodClip.source_project_id == project_id)
    rows = q.order_by(MoodClip.updated_at.desc()).limit(100).all()
    return {"clips": [clip_dict(r, with_candidates=False) for r in rows]}


@router.post("")
async def create_clip(body: ClipCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    _validate_common(body.theme, body.custom_theme, body.duration_s, body.direction, body.mode)
    if body.source_project_id is not None:
        project = db.get(Project, body.source_project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        assert_project_owner(project)
    row = MoodClip(
        user_id=current_user_id.get(),
        source_project_id=body.source_project_id,
        mode=body.mode,
        theme=body.theme,
        custom_theme=body.custom_theme.strip()[:120],
        duration_s=body.duration_s,
        direction=body.direction,
        inspiration=body.inspiration.strip()[:500],
        **_norm_steering(
            body.dialogue_style, body.pacing, body.intensity, body.style_hints
        ),
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
    for name, value in _norm_steering(
        body.dialogue_style, body.pacing, body.intensity, body.style_hints
    ).items():
        setattr(row, name, value)
    db.commit()
    return {"clip_row": clip_dict(row)}


@router.post("/{clip_id}/generate")
async def generate_clip(
    clip_id: int, body: GenerateIn | None = None, db: Session = Depends(get_db)
):
    _get_clip(db, clip_id)
    feedback = (body.feedback if body else "").strip()[:200]
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
            return await generate_batch(session, row, progress, feedback=feedback)

    return {"job_id": spawn_job(kind, work)}


@router.post("/{clip_id}/reexpand")
async def reexpand_clip(clip_id: int, body: ReexpandIn, db: Session = Depends(get_db)):
    """单条重拍:方向对但执行差(分镜平/台词多)时,保切入与画风只重展开这条。"""
    _get_clip(db, clip_id)
    kind = f"clips-reexp-{clip_id}"
    for jid, job in list_running("clips-"):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            row = session.get(MoodClip, clip_id)
            if row is None:
                raise ValueError("短片已被删除,任务取消。")
            return await reexpand_batch(
                session, row, body.index, body.feedback.strip()[:200], progress
            )

    return {"job_id": spawn_job(kind, work)}


@router.put("/{clip_id}/clip")
async def save_clip_card(clip_id: int, body: ClipCardIn, db: Session = Depends(get_db)):
    """手卡编辑保存:归一化(与批产同口径)、重算切段与警示,选定本子与候选保持同步。"""
    row = _get_clip(db, clip_id)
    if row.chosen < 0 or not (row.clip or {}).get("shots"):
        raise HTTPException(status_code=400, detail="先「三选一」选定本子,再编辑手卡。")

    from app.engines.clips.batch import _build_candidate

    card = body.card or {}
    old = row.clip or {}
    take = {
        "take": str(card.get("take") or old.get("take") or "").strip()[:60],
        "logline": str(card.get("logline") or old.get("logline") or "").strip()[:200],
        "emotion_curve": str(card.get("emotion_curve") or old.get("emotion_curve") or "").strip()[:120],
        "punchline": str(card.get("punchline") or old.get("punchline") or "").strip()[:60],
        "hook_text": str(card.get("hook_text") or old.get("hook_text") or "").strip()[:60],
        "quote_source": str(card.get("quote_source") or old.get("quote_source") or "").strip()[:300],
    }
    style = {
        "style_cn": row.style_cn or "",
        "style_en": row.style_en or "",
        "negative": row.negative or "",
    }
    # 小说衍生要保留金句溯源:与批产同一套 cautions 口径
    excerpts = ""
    if row.source_project_id:
        from app.engines.clips.batch import _novel_context

        project = db.get(Project, row.source_project_id)
        if project is not None:
            excerpts, _ = _novel_context(db, project)
    saved = _build_candidate(
        take,
        {"lines": card.get("lines") or [], "shots": card.get("shots") or []},
        style,
        row.duration_s,
        excerpts=excerpts,
    )
    if saved is None:
        raise HTTPException(status_code=400, detail="分镜为空:至少保留一格有效画面。")
    row.clip = saved
    candidates = list(row.candidates or [])
    if 0 <= row.chosen < len(candidates):
        candidates[row.chosen] = saved
        row.candidates = candidates
    db.commit()
    return {"clip_row": clip_dict(row)}


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
    # 生成/重拍中拒绝删除:任务收尾要 UPDATE 这一行,行没了会 StaleDataError,
    # 几分钟的批产白跑还报一条费解的错(线上实测 21 分钟后崩在收尾)。
    for prefix in (f"clips-gen-{clip_id}", f"clips-reexp-{clip_id}"):
        for _jid, job in list_running(prefix):
            raise HTTPException(
                status_code=409,
                detail="这条短片正在生成/重拍中,等它跑完再删除(刷新页面可看进度)。",
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
