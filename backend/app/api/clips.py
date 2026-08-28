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
GET    /api/clips/{id}/shoot               出片工作台(按选定手卡切段,首次访问自动建盘)
PUT    /api/clips/{id}/shoot               整卡更新(勾完成/回填成品/写备注/同步外链参考图)
POST   /api/clips/{id}/shoot/{i}/reference            段参考图上传(multipart)
POST   /api/clips/{id}/shoot/{i}/reference/link       段参考图外链
DELETE /api/clips/{id}/shoot/{i}/reference/{j}        删段参考图(上传连文件删)
GET    /api/clips/{id}/shoot/{i}/reference/{j}        读段参考图(鉴权)
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import storage
from app.auth import assert_project_owner, get_current_user
from app.db.models import ClipShoot, MoodClip, Project, RenderTask
from app.db.session import get_db
from app.engines.clips import (
    ClipBatchError,
    build_clip_film_prompt,
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
from app.engines.media.text import clip
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


class FilmPromptIn(BaseModel):
    """整片提示词手动保存:整段替换(粘贴自己写的版本也走这里)。"""
    film_prompt: str = ""


class ShootUpdateIn(BaseModel):
    """出片工作台整卡更新:前端按段归并好后整卡回传(段数 ≤ 个位数,整卡写一次更安全)。"""
    shoot: list[dict] = []


class RefLinkIn(BaseModel):
    """贴一张段参考图外链(生图站的图片地址,可能带时效签名)。"""
    url: str = ""
    note: str = ""


def _get_clip(db: Session, clip_id: int) -> MoodClip:
    row = db.get(MoodClip, clip_id)
    if row is None:
        raise HTTPException(status_code=404, detail="短片不存在")
    assert_project_owner(row)
    return row


# ---- 出片工作台 ---------------------------------------------
# 状态存 ClipShoot.shoot(按段 index → {ref_images, done, result_link, note}),与手卡的
# clip.chunks 是两份独立数据:手卡重算切段后前端按 index 归并,不再持有的段忽略、新段无状态。

def _shoot_unit(chunk: dict) -> dict:
    """从手卡的一个切段块派生「空段」出片单元:只带元信息,出片状态清零。"""
    return {
        "index": int(chunk.get("index", 0) or 0),
        "start_s": int(chunk.get("start_s", 0) or 0),
        "end_s": int(chunk.get("end_s", 0) or 0),
        "duration_s": int(chunk.get("duration_s", 0) or 0),
        "over_limit": bool(chunk.get("over_limit")),
        "subtitle": str(chunk.get("subtitle") or ""),
        "shot_seqs": list(chunk.get("shot_seqs") or []),
        "scenes": list(chunk.get("scenes") or []),
        "ref_images": [],
        "done": False,
        "result_link": "",
        "note": "",
    }


def _get_shoot_row(db: Session, clip_id: int) -> ClipShoot:
    """取该短片的出片工作台;没有就在第一次访问时按选定手卡的切段自动建盘。

    懒建而不是建短片时就建:短片刻意到「选定本子、手卡就绪」才值得搭工作台,
    此时 clip.chunks 才存在,建出来的段才有 index 可对齐。
    """
    row = db.query(ClipShoot).filter(ClipShoot.clip_id == clip_id).first()
    if row is not None:
        return row
    clip_row = db.get(MoodClip, clip_id)
    chunks = (clip_row.clip or {}).get("chunks") or []
    row = ClipShoot(
        user_id=clip_row.user_id,
        clip_id=clip_id,
        shoot=[_shoot_unit(c) for c in chunks],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _find_shoot_unit(shoot: list | None, index: int) -> dict:
    """按段号找单元;找不到说明手卡已重排,让前端重新打开工作台归并。"""
    for unit in shoot or []:
        if isinstance(unit, dict) and int(unit.get("index", -1) or -1) == index:
            return unit
    raise HTTPException(
        status_code=404,
        detail="这个段没在工作台里,刷新一下让工作台跟新手卡对齐。",
    )


def _clean_refs(refs) -> list[dict]:
    """整卡回传时收敛 ref_images:只留合法形态(kind∈upload/url,src 非空)。"""
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


def _clean_shoot(units) -> list[dict]:
    """整卡回传时归一化:收敛字段类型与长度,不信任客户端塞过来的任意结构。"""
    cleaned: list[dict] = []
    for u in units or []:
        if not isinstance(u, dict):
            continue
        try:
            index = int(u.get("index", 0) or 0)
        except (TypeError, ValueError):
            continue
        cleaned.append({
            "index": index,
            "start_s": int(u.get("start_s", 0) or 0),
            "end_s": int(u.get("end_s", 0) or 0),
            "duration_s": int(u.get("duration_s", 0) or 0),
            "over_limit": bool(u.get("over_limit")),
            "subtitle": str(u.get("subtitle") or ""),
            "shot_seqs": list(u.get("shot_seqs") or []),
            "scenes": list(u.get("scenes") or []),
            "ref_images": _clean_refs(u.get("ref_images")),
            "done": bool(u.get("done")),
            "result_link": clip(u.get("result_link"), 500),
            "note": clip(u.get("note"), 500),
        })
    return cleaned


def _validate_common(
    theme: str, custom_theme: str, duration_s: int, direction: str,
    mode: str = "mood", inspiration: str = "",
) -> None:
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"未知工坊类型:{mode}")
    if mode == "free":
        # 故事工坊没有命题目录,用户点子(inspiration)就是必填的"主题";
        # 标题(custom_theme)可选,theme 恒空。
        if not inspiration.strip():
            raise HTTPException(status_code=400, detail="先把你的点子写下来(一句话到一段话都行)。")
    else:
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
        # 小说「投流」页签:只看该书衍生的企划
        q = q.filter(MoodClip.source_project_id == project_id)
    else:
        # 独立工坊(情绪短片/灵感工坊):只看工坊自建的条目,小说衍生的投流
        # 企划留在小说页签里。曾因缺这道过滤,小说企划混进工坊列表且界面
        # 零区分,用户点进工作台、返回又按数据归属跳回小说书页,以为工坊
        # 内容丢失(实测复现)。
        q = q.filter(MoodClip.source_project_id.is_(None))
    rows = q.order_by(MoodClip.updated_at.desc()).limit(100).all()
    return {"clips": [clip_dict(r, with_candidates=False) for r in rows]}


@router.post("")
async def create_clip(body: ClipCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    _validate_common(
        body.theme, body.custom_theme, body.duration_s, body.direction,
        body.mode, inspiration=body.inspiration,
    )
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


# ---- 整片提示词(端到端音频原生视频模型) ----


@router.post("/{clip_id}/film-prompt")
async def build_film_prompt(clip_id: int, db: Session = Depends(get_db)):
    """把选中本子的分镜+台词+风格组装成一条「一次出一整片」的成片提示词(覆盖旧稿)。"""
    _get_clip(db, clip_id)
    kind = f"clips-film-prompt-{clip_id}"
    for jid, job in list_running("clips-"):
        if job["kind"] == kind:
            return {"job_id": jid}

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            row = session.get(MoodClip, clip_id)
            if row is None:
                raise ValueError("短片已被删除,任务取消。")
            return await build_clip_film_prompt(session, row, progress)

    return {"job_id": spawn_job(kind, work)}


@router.get("/{clip_id}/film-prompt")
async def get_film_prompt(clip_id: int, db: Session = Depends(get_db)):
    row = _get_clip(db, clip_id)
    return {"film_prompt": row.film_prompt or ""}


@router.put("/{clip_id}/film-prompt")
async def save_film_prompt(
    clip_id: int, body: FilmPromptIn, db: Session = Depends(get_db)
):
    """整段替换保存:手改后的稿子、或用户自己写的版本都存这一列。"""
    row = _get_clip(db, clip_id)
    row.film_prompt = (body.film_prompt or "").strip()
    db.commit()
    return {"film_prompt": row.film_prompt}


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
    db.query(ClipShoot).filter(ClipShoot.clip_id == clip_id).delete()
    # 出片任务与渲染草片跟着短片走:行删掉后再清文件(与参考图目录同序)
    from app.api.render import purge_render_tasks

    render_files = [
        t.result_path
        for t in db.query(RenderTask).filter(RenderTask.clip_id == clip_id)
        if t.result_path
    ]
    purge_render_tasks(db, clip_id=clip_id)
    db.delete(row)
    db.commit()
    # 出片参考图按 clip 号独占 clips/<id>/ 目录,行走了文件不能留在卷里吃配额
    storage.delete_clip_dir(clip_id)
    for rel in render_files:
        storage.delete_render_file(rel)
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


# ---- 出片工作台:按段出片(参考图/状态/成品),minimax 等图文生视频一键搬运 ----

@router.get("/{clip_id}/shoot")
async def get_shoot(clip_id: int, db: Session = Depends(get_db)):
    """读出片工作台(首次访问自动按选定手卡切段建盘)。"""
    _get_clip(db, clip_id)
    return {"shoot": _get_shoot_row(db, clip_id).shoot or []}


@router.put("/{clip_id}/shoot")
async def update_shoot(clip_id: int, body: ShootUpdateIn, db: Session = Depends(get_db)):
    """整卡更新:勾完成/回填成品链接/写备注/同步外链参考图,前端归并好后整卡回传。"""
    _get_clip(db, clip_id)
    row = _get_shoot_row(db, clip_id)
    row.shoot = _clean_shoot(body.shoot)
    db.commit()
    return {"shoot": row.shoot}


@router.post("/{clip_id}/shoot/{index}/reference")
async def upload_shoot_reference(
    clip_id: int, index: int, file: UploadFile = File(...), note: str = Form(""),
    db: Session = Depends(get_db),
):
    """给一段传一张参考图(人物定妆/关键帧实拍,minimax 生视频时丢给它当锚)。"""
    _get_clip(db, clip_id)
    row = _get_shoot_row(db, clip_id)
    unit = _find_shoot_unit(row.shoot, index)
    if len(unit["ref_images"]) >= storage.MAX_REFS_PER_SEGMENT:
        raise HTTPException(
            status_code=400,
            detail=f"每段最多 {storage.MAX_REFS_PER_SEGMENT} 张参考图,先删掉一张再传。",
        )
    data = await file.read(storage.MAX_IMAGE_BYTES + 1)
    try:
        rel = storage.save_clip_ref(clip_id, index, data, len(unit["ref_images"]))
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    unit["ref_images"] = unit["ref_images"] + [
        {"kind": "upload", "src": rel, "note": clip(note, 100)}
    ]
    # 原地改了 shoot 里的嵌套结构,SQLAlchemy 的 JSON 列不会自动标记 dirty,
    # 不手动 flag_modified 的话 commit 不落盘(整卡 PUT 是整体赋值所以没事)。
    flag_modified(row, "shoot")
    db.commit()
    return {"shoot": row.shoot}


@router.post("/{clip_id}/shoot/{index}/reference/link")
async def link_shoot_reference(
    clip_id: int, index: int, body: RefLinkIn, db: Session = Depends(get_db)
):
    """给一段贴一张参考图外链(生图站地址;带时效签名会失效,前端要提示建议下载后上传)。"""
    _get_clip(db, clip_id)
    row = _get_shoot_row(db, clip_id)
    unit = _find_shoot_unit(row.shoot, index)
    if len(unit["ref_images"]) >= storage.MAX_REFS_PER_SEGMENT:
        raise HTTPException(
            status_code=400,
            detail=f"每段最多 {storage.MAX_REFS_PER_SEGMENT} 张参考图,先删掉一张再贴。",
        )
    url = clip(body.url, 500)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填 http/https 开头的图片地址。")
    unit["ref_images"] = unit["ref_images"] + [
        {"kind": "url", "src": url, "note": clip(body.note, 100)}
    ]
    flag_modified(row, "shoot")
    db.commit()
    return {"shoot": row.shoot}


@router.delete("/{clip_id}/shoot/{index}/reference/{img_index}")
async def delete_shoot_reference(
    clip_id: int, index: int, img_index: int, db: Session = Depends(get_db)
):
    """删一段的一张参考图(上传的连文件一起删)。"""
    _get_clip(db, clip_id)
    row = _get_shoot_row(db, clip_id)
    unit = _find_shoot_unit(row.shoot, index)
    refs = unit["ref_images"]
    if not 0 <= img_index < len(refs):
        raise HTTPException(status_code=404, detail="这张参考图不存在。")
    gone = refs.pop(img_index)
    if gone["kind"] == "upload":
        storage.delete(gone["src"])
    flag_modified(row, "shoot")
    db.commit()
    return {"shoot": row.shoot}


@router.get("/{clip_id}/shoot/{index}/reference/{img_index}")
async def read_shoot_reference(
    clip_id: int, index: int, img_index: int, db: Session = Depends(get_db)
):
    """读一段上传的参考图(走鉴权;上传目录不挂静态服务,<img> 由前端转 blob 显示)。"""
    _get_clip(db, clip_id)
    row = _get_shoot_row(db, clip_id)
    unit = _find_shoot_unit(row.shoot, index)
    refs = unit["ref_images"]
    if not 0 <= img_index < len(refs) or refs[img_index]["kind"] != "upload":
        raise HTTPException(status_code=404, detail="这张参考图不存在。")
    rel = refs[img_index]["src"]
    try:
        path = storage.resolve(rel)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="参考图文件已丢失。")
    return Response(
        content=path.read_bytes(), media_type=storage.content_type_of(rel)
    )
