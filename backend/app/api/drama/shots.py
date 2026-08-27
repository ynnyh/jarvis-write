# app/api/drama/shots.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 分镜手动编辑 / 逐格挂素材路由。

拆分自原 app/api/drama.py。包含分镜的 PATCH 编辑、单格重出提示词，以及
静帧素材的上传、外链、删除、读取。
"""
from __future__ import annotations

from fastapi import Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app import storage
from app.api.drama._common import (
    RefLinkIn,
    ShotIn,
    ShotPromptIn,
    _episode_job,
    _get_shot,
    _load_for_job,
    clip,
    DramaShot,
    get_db,
    make_sub_router,
    Project,
    render_single_shot_prompt,
    shot_asset_list,
    shots_payload,
    spawn_job,
)

router = make_sub_router()


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
