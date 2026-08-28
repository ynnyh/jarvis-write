# app/api/render.py
# -*- coding: utf-8 -*-
"""出片引擎接口(轻量档):配置 + 漫剧/情绪两条线的提交、版本历史、采用、读取。

架构见 docs/adr/0003:工坊是控制面,生成本身外包给 autodl.art 的 ComfyUI 工作流。
提交即建 RenderTask(queued)并起后台任务;版本历史支撑「重 roll 攒版本、挑一版」;
「采用」只回写各线自己的指针字段(drama_shots.clip_ref / ClipShoot.shoot[].result_link),
打勾(done_video/done)永远留给人工——草片好不好,引擎说了不算。

GET  /api/render/config                                        出片配置(token 打码)
PUT  /api/render/config                                        保存(token 留空不改)
POST /api/projects/{pid}/drama/shots/{sid}/render              提交一格出片(异步)
GET  /api/projects/{pid}/drama/shots/{sid}/render/tasks        该格版本历史
POST /api/clips/{cid}/shoot/{i}/render                         提交一段出片(异步)
GET  /api/clips/{cid}/shoot/{i}/render/tasks                   该段版本历史
POST /api/render/tasks/{tid}/adopt                             改用某版当成片
GET  /api/render/tasks/{tid}/file                              读草片文件(鉴权)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import storage
from app.api.clips import _get_clip
from app.api.drama._common import _get_shot, get_project_or_404
from app.api.settings import _mask
from app.auth import get_current_user
from app.crypto import decrypt, encrypt
from app.db.models import (
    DramaEpisode,
    DramaShot,
    DramaStyleCard,
    Project,
    RenderConfig,
    RenderTask,
)
from app.db.session import get_db
from app.engines.clips.render_input import chunk_render_payload
from app.engines.drama.common import (
    character_anchor_maps,
    match_character,
    shot_asset_list,
)
from app.engines.drama.production import match_speaker
from app.engines.drama.video import api_render_payload
from app.engines.render.service import apply_pointer, start_render
from app.jobs import list_running, spawn_job
from app.net_guard import assert_public_base_url

logger = logging.getLogger("jarvis-write.render")

router = APIRouter(tags=["render"], dependencies=[Depends(get_current_user)])

_VALID_RESOLUTIONS = ("480p", "768p")
_TASK_LIST_LIMIT = 20


# =============== 出片配置(每用户一份)===============


def _get_config_row(db: Session, user_id: int) -> RenderConfig | None:
    return db.query(RenderConfig).filter(RenderConfig.user_id == user_id).first()


def _config_out(row: RenderConfig | None) -> dict:
    from app.engines.render.ffmpeg import available as ffmpeg_available

    token = decrypt(row.token) if row else ""
    return {
        "base_url": row.base_url if row else "",
        "token_masked": _mask(token),
        "has_token": bool(token),
        "resolution": row.resolution if row else "768p",
        "workflow_i2v": row.workflow_i2v if row else "",
        "workflow_t2v": row.workflow_t2v if row else "",
        "workflow_tts": row.workflow_tts if row else "",
        "workflow_talk": row.workflow_talk if row else "",
        # 前端空态引导用:没填 token 就把出片按钮换成「先去设置」
        "configured": bool(token),
        # 末帧自动接力:部署里有 ffmpeg 才亮(缺失时前端整体隐藏,零影响)
        "last_frame_available": ffmpeg_available(),
    }


class RenderConfigIn(BaseModel):
    base_url: str = ""
    # 留空/不传 = 不改动已存 token(与 LLM key 同一交互约定)
    token: str | None = Field(default=None, description="留空 = 不改动已存 token")
    resolution: str = "768p"
    workflow_i2v: str = ""
    workflow_t2v: str = ""
    workflow_tts: str = ""
    workflow_talk: str = ""


@router.get("/api/render/config")
async def get_render_config(
    db: Session = Depends(get_db), user=Depends(get_current_user)
):
    return _config_out(_get_config_row(db, user.id))


@router.put("/api/render/config")
async def save_render_config(
    req: RenderConfigIn, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    if req.base_url.strip():
        assert_public_base_url(req.base_url.strip())  # SSRF 防线,照 LLM 配置同款
    if req.resolution not in _VALID_RESOLUTIONS:
        raise HTTPException(
            status_code=400, detail=f"分辨率只支持 {'/'.join(_VALID_RESOLUTIONS)}。"
        )
    row = _get_config_row(db, user.id)
    if row is None:
        from app.db.models.render import (
            DEFAULT_RENDER_BASE_URL,
            DEFAULT_WORKFLOW_I2V,
            DEFAULT_WORKFLOW_T2V,
        )

        row = RenderConfig(
            user_id=user.id,
            base_url=DEFAULT_RENDER_BASE_URL,
            workflow_i2v=DEFAULT_WORKFLOW_I2V,
            workflow_t2v=DEFAULT_WORKFLOW_T2V,
        )
        db.add(row)
    if req.base_url.strip():
        row.base_url = req.base_url.strip()
    if req.token:  # 空串/不传 = 不改动已存 token
        row.token = encrypt(req.token.strip())
    row.resolution = req.resolution
    row.workflow_i2v = req.workflow_i2v.strip() or row.workflow_i2v
    row.workflow_t2v = req.workflow_t2v.strip() or row.workflow_t2v
    row.workflow_tts = req.workflow_tts.strip() or row.workflow_tts
    row.workflow_talk = req.workflow_talk.strip() or row.workflow_talk
    db.commit()
    return _config_out(row)


def _require_config(db: Session, user_id: int) -> RenderConfig:
    """出片前置:必须已配 token,没配就给「去设置」的引导语(前端空态同款文案)。"""
    row = _get_config_row(db, user_id)
    if row is None or not decrypt(row.token):
        raise HTTPException(
            status_code=400,
            detail="还没有配置出片引擎:请先到「设置 → 出片引擎」填写 autodl.art 的令牌(token)。",
        )
    return row


# =============== 提交出片(漫剧 = 格 / 情绪 = 段)===============


def _task_out(t: RenderTask) -> dict:
    params = dict(t.params or {})
    if params.get("prompt"):
        params["prompt"] = str(params["prompt"])[:400]
    return {
        "id": t.id,
        "line": t.line,
        "kind": t.kind,
        "workflow_id": t.workflow_id,
        "provider_task_id": t.provider_task_id,
        "status": t.status,
        "params": params,
        "result_path": t.result_path,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else "",
    }


def _dedup_running(kind: str) -> str | None:
    """同单元已有出片任务在跑就别再起一个(断线重连直接复用旧任务)。"""
    running = list_running(kind)
    return running[0][0] if running else None


def _create_task(
    db: Session, user_id: int, *, line: str, kind: str, workflow_id: str,
    params: dict, project_id: int | None, shot_id: int | None = None,
    clip_id: int | None = None, chunk_index: int = -1,
) -> RenderTask:
    task = RenderTask(
        user_id=user_id,
        line=line,
        kind=kind,
        workflow_id=workflow_id,
        params=params,
        project_id=project_id,
        shot_id=shot_id,
        clip_id=clip_id,
        chunk_index=chunk_index,
        status="queued",
    )
    db.add(task)
    db.commit()
    return task


def _find_speaker_voice(db: Session, project_id: int, shot: DramaShot) -> tuple[str, str]:
    """对白格 → (说话人, 音色参考路径)。反推口径与成片包配音稿同一套。

    剧本 lines 反推 speaker(精确→模糊),再按名/别名匹配角色卡取 voice_ref;
    任何一环断了(无剧本/匹配不到/角色没传音色)都返回空,由调用方回退普通出片。
    """
    text = (shot.dialogue or "").strip()
    if not text:
        return "", ""
    ep = db.get(DramaEpisode, shot.episode_id)
    lines = ((ep.script or {}).get("lines") or []) if ep else []
    speaker = match_speaker(text, [l for l in lines if isinstance(l, dict)])
    if not speaker:
        return "", ""
    by_name, by_alias = character_anchor_maps(db, project_id)
    card = match_character(speaker, by_name, by_alias)
    if card is None:
        return speaker, ""
    return speaker, (getattr(card, "voice_ref", "") or "")


@router.post("/api/projects/{project_id}/drama/shots/{shot_id}/render")
async def submit_drama_render(
    project_id: int, shot_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """提交一格分镜出片。路由优先级:

    完整档 + 有台词 + 匹配到说话人音色 + 有静帧 → **talk**(配音对口型);
    否则有静帧走首尾帧(i2v)、没静帧走文生视频(t2v)。回退原因写进
    task.params.note,不静默——用户得知道这格为什么没开口说话。
    """
    shot = _get_shot(db, project_id, shot_id)
    cfg = _require_config(db, user.id)
    kind = "render:drama:shot:" + str(shot_id)
    if (jid := _dedup_running(kind)) :
        return {"job_id": jid, "task_id": None, "deduped": True}

    assets = shot_asset_list(shot)
    style = (
        db.query(DramaStyleCard).filter(DramaStyleCard.project_id == project_id).first()
    )
    project = db.get(Project, project_id)
    quality = cfg.resolution

    # ---- 对白链判定(完整档)----
    talk = None
    note = ""
    if (
        (shot.dialogue or "").strip()
        and project is not None
        and project.render_mode == "full"
    ):
        speaker, voice_src = _find_speaker_voice(db, project_id, shot)
        if not assets:
            note = "完整档:本格还没挂静帧,对白配音对口型缺首帧图——先出图挂静帧再出片。"
        elif not voice_src:
            who = f"「{speaker}」" if speaker else "说话角色"
            note = (
                f"完整档:{who}还没传音色参考,本格走了普通出片"
                "(到角色卡传 5-10 秒人声即可配音对口型)。"
            )
        else:
            talk = {
                "user_id": user.id,
                "text": shot.dialogue.strip(),
                "voice_src": voice_src,
                "emotion": shot.emotion or "",
                "workflow_tts": cfg.workflow_tts,
            }

    if talk:
        kind_flow = "talk"
        workflow_id = cfg.workflow_talk
        payload = {
            "resolution": api_render_payload(shot, style, quality=quality)["resolution"],
            "text": shot.dialogue.strip()[:400],
            "emotion": shot.emotion or "",
        }
    else:
        kind_flow = "i2v" if assets else "t2v"
        workflow_id = cfg.workflow_i2v if kind_flow == "i2v" else cfg.workflow_t2v
        payload = api_render_payload(shot, style, quality=quality)
        if note:
            payload["note"] = note
    task = _create_task(
        db, user.id, line="drama", kind=kind_flow, workflow_id=workflow_id,
        params=payload, project_id=project_id, shot_id=shot_id,
    )
    spec = {
        "task_id": task.id, "user_id": user.id, "line": "drama",
        "shot_id": shot_id, "clip_id": None, "chunk_index": -1,
        "kind": kind_flow, "workflow_id": workflow_id, "params": payload,
        "talk": talk,
        "first_frame": (
            {"src": assets[0]["src"], "kind": assets[0]["kind"]}
            if assets else None
        ),
    }

    async def work(progress):
        return await start_render(progress, spec)

    return {"job_id": spawn_job(kind, work), "task_id": task.id, "deduped": False}


@router.get("/api/projects/{project_id}/drama/shots/{shot_id}/render/tasks")
async def list_drama_render_tasks(
    project_id: int, shot_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    _get_shot(db, project_id, shot_id)  # 归属校验
    rows = (
        db.query(RenderTask)
        .filter(RenderTask.user_id == user.id, RenderTask.shot_id == shot_id)
        .order_by(RenderTask.id.desc())
        .limit(_TASK_LIST_LIMIT)
        .all()
    )
    return {"tasks": [_task_out(t) for t in rows]}


@router.post("/api/clips/{clip_id}/shoot/{chunk_index}/render")
async def submit_clips_render(
    clip_id: int, chunk_index: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """提交一个切段出片:首帧用该段挂的第一张参考图,没有就文生视频。"""
    mood = _get_clip(db, clip_id)
    cfg = _require_config(db, user.id)
    kind = f"render:clips:{clip_id}:{chunk_index}"
    if (jid := _dedup_running(kind)):
        return {"job_id": jid, "task_id": None, "deduped": True}

    chunks = (mood.clip or {}).get("chunks") or []
    if not 0 <= chunk_index < len(chunks):
        raise HTTPException(status_code=404, detail="这个段不存在(手卡可能重新切过段)。")
    chunk = chunks[chunk_index]

    from app.db.models import ClipShoot

    shoot_row = db.query(ClipShoot).filter(ClipShoot.clip_id == clip_id).first()
    refs = []
    if shoot_row is not None and 0 <= chunk_index < len(shoot_row.shoot or []):
        refs = shoot_row.shoot[chunk_index].get("ref_images") or []
    has_frame = bool(refs)
    kind_flow = "i2v" if has_frame else "t2v"
    payload = chunk_render_payload(mood, chunk, quality=cfg.resolution)
    workflow_id = cfg.workflow_i2v if kind_flow == "i2v" else cfg.workflow_t2v
    task = _create_task(
        db, user.id, line="clips", kind=kind_flow, workflow_id=workflow_id,
        params=payload, project_id=None, clip_id=clip_id, chunk_index=chunk_index,
    )
    spec = {
        "task_id": task.id, "user_id": user.id, "line": "clips",
        "shot_id": None, "clip_id": clip_id, "chunk_index": chunk_index,
        "kind": kind_flow, "workflow_id": workflow_id, "params": payload,
        "first_frame": (
            {"src": refs[0]["src"], "kind": refs[0]["kind"]} if has_frame else None
        ),
    }

    async def work(progress):
        return await start_render(progress, spec)

    return {"job_id": spawn_job(kind, work), "task_id": task.id, "deduped": False}


@router.get("/api/clips/{clip_id}/shoot/{chunk_index}/render/tasks")
async def list_clips_render_tasks(
    clip_id: int, chunk_index: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    _get_clip(db, clip_id)  # 归属校验
    rows = (
        db.query(RenderTask)
        .filter(
            RenderTask.user_id == user.id,
            RenderTask.clip_id == clip_id,
            RenderTask.chunk_index == chunk_index,
        )
        .order_by(RenderTask.id.desc())
        .limit(_TASK_LIST_LIMIT)
        .all()
    )
    return {"tasks": [_task_out(t) for t in rows]}


# =============== 版本采用 / 草片读取 ===============


def _get_own_task(db: Session, task_id: int, user_id: int) -> RenderTask:
    task = db.get(RenderTask, task_id)
    # 不泄露存在性:别人的任务与不存在的任务同样 404
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="出片任务不存在。")
    return task


@router.post("/api/render/tasks/{task_id}/adopt")
async def adopt_render_task(
    task_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """改用某一版草片当成片(只回写指针;打勾仍由人工)。

    各线指针:漫剧 drama_shots.clip_ref;情绪 ClipShoot.shoot[i].result_link。
    """
    task = _get_own_task(db, task_id, user.id)
    if task.status != "success" or not task.result_path:
        raise HTTPException(status_code=400, detail="只有出片成功的版本才能设为成片。")
    if task.line == "drama":
        shot = db.get(DramaShot, task.shot_id) if task.shot_id else None
        if shot is None:
            raise HTTPException(status_code=404, detail="这一格已不存在(可能重拆过分镜)。")
        shot.clip_ref = task.result_path
        db.commit()
        return {"adopted": True, "clip_ref": task.result_path}
    if task.line == "clips":
        from app.db.models import ClipShoot

        row = db.query(ClipShoot).filter(ClipShoot.clip_id == task.clip_id).first()
        if row is None or not 0 <= task.chunk_index < len(row.shoot or []):
            raise HTTPException(status_code=404, detail="这个段已不存在(手卡可能重新切过段)。")
        row.shoot[task.chunk_index]["result_link"] = task.result_path
        flag_modified(row, "shoot")
        db.commit()
        return {"adopted": True, "result_link": task.result_path}
    raise HTTPException(status_code=400, detail="未知的出片线。")


@router.get("/api/render/tasks/{task_id}/file")
async def read_render_task_file(
    task_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """读渲染草片(走鉴权,渲染目录不挂静态服务)。"""
    task = _get_own_task(db, task_id, user.id)
    if not task.result_path:
        raise HTTPException(status_code=404, detail="这一版还没有成片文件。")
    try:
        path = storage.resolve(task.result_path)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="成片文件已丢失。")
    return Response(
        content=path.read_bytes(),
        media_type="video/mp4",
        # 私有资产:允许浏览器本地缓存,但不许中间层/CDN 缓存
        headers={"Cache-Control": "private, max-age=86400"},
    )


# 供级联清理复用(删项目/删短片时把任务行与草片文件一起带走)
def purge_render_tasks(db: Session, *, project_id: int | None = None, clip_id: int | None = None) -> int:
    """删掉归属的渲染任务行与草片文件,返回删除行数(调用方负责在事务里调)。"""
    q = db.query(RenderTask)
    if project_id is not None:
        q = q.filter(RenderTask.project_id == project_id)
    elif clip_id is not None:
        q = q.filter(RenderTask.clip_id == clip_id)
    else:
        return 0
    rows = q.all()
    for t in rows:
        if t.result_path:
            storage.delete_render_file(t.result_path)
        # 末帧文件按任务号约定落位(render/lf/r<id>.png),任务删则末帧删
        lf = storage.upload_root() / "render" / "lf" / f"r{t.id}.png"
        lf.unlink(missing_ok=True)
    for t in rows:
        db.delete(t)
    return len(rows)


# =============== 末帧自动接力(上一镜末帧 → 下一镜首帧)===============


def _lf_path(task_id: int):
    """末帧文件路径(约定落位 render/lf/r<task_id>.png)。"""
    return storage.upload_root() / "render" / "lf" / f"r{int(task_id)}.png"


def _latest_lf_task(db: Session, shot_id: int) -> RenderTask | None:
    """该格最新一个「出片成功且末帧文件还在」的任务;没有则 None。"""
    rows = (
        db.query(RenderTask)
        .filter(RenderTask.shot_id == shot_id, RenderTask.status == "success")
        .order_by(RenderTask.id.desc())
        .limit(_TASK_LIST_LIMIT)
        .all()
    )
    for t in rows:
        if _lf_path(t.id).is_file():
            return t
    return None


@router.get("/api/projects/{project_id}/drama/episodes/{episode_id}/prev-frames")
async def episode_prev_frames(
    project_id: int, episode_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """整集一次拉齐:每个分镜格的「上一格末帧」可用量。

    by_seq 的 key 是分镜格 seq(seq 从 1 起,所以 seq-1 的格才可能有末帧);
    前端据此在第 N 格显示「用上一镜末帧当首帧」的候选按钮。
    """
    from app.db.models import DramaEpisode

    ep = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.id == episode_id, DramaEpisode.project_id == project_id)
        .first()
    )
    _ = get_project_or_404(db, project_id)  # 归属校验(顺序无所谓,先拦人再看集)
    if ep is None:
        raise HTTPException(status_code=404, detail="这一集不存在。")
    from app.db.models import DramaShot

    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode_id)
        .order_by(DramaShot.seq)
        .all()
    )
    by_seq: dict[str, dict] = {}
    for prev, cur in zip(shots, shots[1:]):
        t = _latest_lf_task(db, prev.id)
        if t is not None:
            by_seq[str(cur.seq)] = {"task_id": t.id, "from_seq": prev.seq}
    return {"by_seq": by_seq}


@router.get("/api/render/tasks/{task_id}/last-frame")
async def read_render_last_frame(
    task_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """读某次渲染的末帧 png(走鉴权,同草片文件一个待遇)。"""
    task = _get_own_task(db, task_id, user.id)
    path = _lf_path(task.id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="这一版没有末帧存档(可能部署里没有 ffmpeg)。")
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/api/projects/{project_id}/drama/shots/{shot_id}/adopt-prev-frame")
async def adopt_prev_frame(
    project_id: int, shot_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    """把「上一镜末帧」一键挂为本格静帧(首尾帧接力的机械实现)。

    与手工上传同一落点(save_shot_asset):上一格最新成功草片的末帧 → 本格
    assets + done_still=True。段计划的「首帧图已就位」立刻亮,出片就有衔接。
    """
    shot = _get_shot(db, project_id, shot_id)
    prev = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == shot.episode_id, DramaShot.seq == shot.seq - 1)
        .first()
    )
    task = _latest_lf_task(db, prev.id) if prev is not None else None
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"第 {shot.seq - 1} 格还没有可用的末帧(先出片,且部署里要有 ffmpeg)。",
        )
    assets = shot_asset_list(shot)
    if len(assets) >= storage.MAX_ASSETS_PER_SHOT:
        raise HTTPException(
            status_code=400,
            detail=f"一格最多挂 {storage.MAX_ASSETS_PER_SHOT} 张静帧,先删掉一张再采纳上一镜末帧。",
        )
    png = _lf_path(task.id).read_bytes()
    rel = storage.save_shot_asset(project_id, shot_id, png, len(assets))
    shot.assets = assets + [{"kind": "upload", "src": rel, "note": f"上一镜(第 {prev.seq} 格)末帧"}]
    shot.done_still = True
    db.commit()
    from app.engines.drama.common import shots_payload

    return {"shot": shots_payload(db, project_id, [shot])[0], "from_seq": prev.seq}
