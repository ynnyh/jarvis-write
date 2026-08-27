# app/api/drama/export.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 集导出路由。

拆分自原 app/api/drama.py。支持 md(拍摄手册)/ csv(分镜表)/ json(全量)/
srt(字幕)/ pack(成片包) 五种格式。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.drama._common import (
    _get_episode,
    DramaCharacterCard,
    DramaProductionPack,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    export_csv,
    export_json,
    export_markdown,
    export_pack_markdown,
    export_srt,
    get_db,
    get_project_or_404,
    make_sub_router,
)

router = make_sub_router()


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
