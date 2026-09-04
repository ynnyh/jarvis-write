# app/api/drama/episodes.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 集规划 / 列表 / 详情 / 删除 / 剧本 / 分镜 / 提示词 / 成片包路由。

拆分自原 app/api/drama.py。
"""
from __future__ import annotations

from fastapi import Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import storage

from app.api.drama._common import (
    FilmPromptGenIn,
    FilmPromptIn,
    PlanIn,
    _episode_job,
    _get_episode,
    _load_for_job,
    _require_approved,
    build_episode_film_prompt,
    build_production_pack,
    build_storyboard,
    clips_payload,
    CLIP_LIMIT_DEFAULT,
    DramaCharacterCard,
    DramaEpisode,
    DramaProductionPack,
    DramaShot,
    DramaStyleCard,
    episode_dict,
    get_db,
    get_project_or_404,
    make_sub_router,
    plan_episodes,
    Project,
    render_shot_prompts,
    shot_progress,
    shot_refs_by_seq,
    shots_payload,
    spawn_job,
    VALID_MODES,
    write_episode_script,
)

router = make_sub_router()


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
    if (existing := _episode_job(kind)):
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


class EpisodeFocusIn(BaseModel):
    focus: str = Field(default="", max_length=200, description="本集重点(作者改编意图,可空)")


@router.patch("/episodes/{episode_id}")
async def patch_episode(
    project_id: int, episode_id: int, body: EpisodeFocusIn, db: Session = Depends(get_db)
):
    """改集的可编辑字段:当前只有 focus(本集重点)。

    focus 随集落库,重写剧本时会作为高优先级指令注入——改编意图的落点。
    """
    ep = _get_episode(db, project_id, episode_id)
    ep.focus = body.focus.strip()
    db.commit()
    return {"episode": episode_dict(ep)}


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
    # r2v 主体绑定要按格知道"谁已有定妆照":没有参考图的格,这一版的提示是引导先出定妆照
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .all()
    )
    return {"plan": clips_payload(shots, style, limit_s, refs_by_seq=shot_refs_by_seq(shots, cards))}


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


# =============== 整片提示词(端到端音频原生视频模型) ===============


@router.post("/episodes/{episode_id}/film-prompt")
async def build_film_prompt(
    project_id: int, episode_id: int,
    body: FilmPromptGenIn | None = None, db: Session = Depends(get_db),
):
    """把分镜切成 ≤单段上限的段,组装成 N 段各自独立可用的成片提示词(覆盖旧稿)。"""
    _get_episode(db, project_id, episode_id)
    segment_s = (body.segment_s if body else 15) or 15
    if segment_s not in (15, 30):
        raise HTTPException(status_code=400, detail="单段时长只支持 15 / 30 秒。")
    kind = f"drama-film-prompt-{episode_id}"
    if (existing := _episode_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj, ep = _load_for_job(session, project_id, episode_id)
            return await build_episode_film_prompt(
                session, proj, ep, progress, segment_s=segment_s
            )

    return {"job_id": spawn_job(kind, work)}


@router.get("/episodes/{episode_id}/film-prompt")
async def get_film_prompt(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    ep = _get_episode(db, project_id, episode_id)
    return {"film_prompt": ep.film_prompt or ""}


@router.put("/episodes/{episode_id}/film-prompt")
async def save_film_prompt(
    project_id: int, episode_id: int, body: FilmPromptIn, db: Session = Depends(get_db)
):
    """整段替换保存:手改后的稿子、或用户自己写的版本都存这一列。"""
    ep = _get_episode(db, project_id, episode_id)
    ep.film_prompt = (body.film_prompt or "").strip()
    db.commit()
    return {"film_prompt": ep.film_prompt}


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


# =============== 集级 BGM(整集合成的垫乐;每集一段,重传即换) ===============


@router.post("/episodes/{episode_id}/bgm")
async def upload_episode_bgm(
    project_id: int,
    episode_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传这一集的 BGM(MP3/WAV ≤15MB;固定名重传即换)。合成时低音量垫底。"""
    get_project_or_404(db, project_id)
    _get_episode(db, project_id, episode_id)
    data = await file.read(storage.MAX_BGM_BYTES + 1)
    try:
        rel = storage.save_episode_bgm(project_id, episode_id, data)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"bgm": rel}


@router.get("/episodes/{episode_id}/bgm")
async def read_episode_bgm(
    project_id: int, episode_id: int, db: Session = Depends(get_db)
):
    """试听这一集的 BGM(走鉴权,上传目录不挂静态服务)。"""
    get_project_or_404(db, project_id)
    _get_episode(db, project_id, episode_id)  # 归属校验
    # 路径规则在 storage(bgm<集号>.<ext>),按约定直接探测两种扩展名
    prefix = f"drama/{project_id}/bgm{episode_id}."
    for ext in ("mp3", "wav"):
        rel = prefix + ext
        try:
            path = storage.resolve(rel)
        except storage.UploadError:
            continue
        if path.is_file():
            return Response(
                content=path.read_bytes(),
                media_type=storage.content_type_of(rel),
                headers={"Cache-Control": "private, max-age=86400"},
            )
    raise HTTPException(status_code=404, detail="这一集还没有 BGM,先上传一首。")


@router.delete("/episodes/{episode_id}/bgm")
async def delete_episode_bgm(
    project_id: int, episode_id: int, db: Session = Depends(get_db)
):
    """删掉这一集的 BGM(连文件一起删;合成时就不垫音乐了)。"""
    get_project_or_404(db, project_id)
    _get_episode(db, project_id, episode_id)
    removed = False
    for ext in ("mp3", "wav"):
        rel = f"drama/{project_id}/bgm{episode_id}.{ext}"
        try:
            path = storage.resolve(rel)
        except storage.UploadError:
            continue
        if path.is_file():
            path.unlink()
            removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="这一集还没有 BGM。")
    return {"ok": True}
