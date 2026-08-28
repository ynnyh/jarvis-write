# app/api/drama/characters.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 角色卡 / 场景卡 / 定妆照路由。

拆分自原 app/api/drama.py。包含角色卡列表、生成、编辑、重出，以及定妆照的
提示词生成、上传、外链、删除、读取。
"""
from __future__ import annotations

from fastapi import Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app import storage
from app.api.drama._common import (
    CharacterCardIn,
    RefLinkIn,
    RefPromptIn,
    _existing_job,
    _get_character,
    character_card_dict,
    clip,
    DramaCharacterCard,
    DramaSceneCard,
    generate_assets,
    generate_ref_sheets,
    generate_voice_cast,
    get_db,
    get_project_or_404,
    make_sub_router,
    Project,
    ref_image_list,
    regenerate_character_card,
    scene_card_dict,
    spawn_job,
    style_card,
    VALID_GENDERS,
)

router = make_sub_router()


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


# =============== 音色参考音频(完整档对白链:indextts2 克隆原料) ===============


@router.post("/characters/{card_id}/voice")
async def upload_character_voice(
    project_id: int,
    card_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传该角色的音色参考(5-10 秒干净人声,MP3/WAV,≤8MB;重传即换)。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    data = await file.read(storage.MAX_AUDIO_BYTES + 1)
    try:
        rel = storage.save_character_voice(project_id, card_id, data)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 换扩展名重传(mp3→wav)时旧文件名不同,先清掉,不然卷里留幽灵音频
    if card.voice_ref and card.voice_ref != rel:
        storage.delete(card.voice_ref)
    card.voice_ref = rel
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


@router.get("/characters/{card_id}/voice")
async def read_character_voice(
    project_id: int, card_id: int, db: Session = Depends(get_db)
):
    """试听该角色的音色参考(走鉴权,同定妆照的读取思路)。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    if not card.voice_ref:
        raise HTTPException(status_code=404, detail="这个角色还没有音色参考。")
    try:
        path = storage.resolve(card.voice_ref)
    except storage.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件已丢失,请重新上传。")
    return Response(
        content=path.read_bytes(),
        media_type=storage.content_type_of(card.voice_ref),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/characters/{card_id}/voice")
async def delete_character_voice(
    project_id: int, card_id: int, db: Session = Depends(get_db)
):
    """删掉该角色的音色参考(连文件一起删;对白格随即回退普通出片)。"""
    get_project_or_404(db, project_id)
    card = _get_character(db, project_id, card_id)
    if not card.voice_ref:
        raise HTTPException(status_code=404, detail="这个角色还没有音色参考。")
    storage.delete(card.voice_ref)
    card.voice_ref = ""
    db.commit()
    return {"card": character_card_dict(card, style_card(db, project_id))}


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
