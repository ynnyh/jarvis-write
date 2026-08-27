# app/api/drama/trailer.py
# -*- coding: utf-8 -*-
"""漫剧工坊 - 预告片路由。

拆分自原 app/api/drama.py。包含预告片的生成、读取和导出。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.drama._common import (
    TrailerIn,
    _existing_job,
    export_trailer_markdown,
    export_trailer_srt,
    DramaTrailer,
    get_db,
    get_project_or_404,
    make_sub_router,
    Project,
    generate_trailer,
    spawn_job,
)

router = make_sub_router()


# =============== 预告片(项目级,一条,重建覆盖) ===============


@router.post("/trailer/generate")
async def generate_trailer_ep(
    project_id: int, body: TrailerIn, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
    kind = f"drama-trailer-{project_id}"
    if (existing := _existing_job("drama-", kind)):
        return existing
    from_ep, to_ep, target_s = body.from_ep, body.to_ep, body.target_s

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            proj = session.get(Project, project_id)
            return await generate_trailer(session, proj, from_ep, to_ep, target_s, progress)

    return {"job_id": spawn_job(kind, work)}


@router.get("/trailer")
async def get_trailer(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    row = (
        db.query(DramaTrailer).filter(DramaTrailer.project_id == project_id).first()
    )
    if row is None:
        return {"trailer": None}
    shots = row.shots or []
    return {
        "trailer": {
            "target_s": row.target_s,
            "title": row.title,
            "lines": row.lines or [],
            "shots": shots,
            "totals": {
                "shots": len(shots),
                "duration_s": sum(int(s.get("duration_s") or 0) for s in shots),
            },
        }
    }


@router.get("/trailer/export")
async def export_trailer(
    project_id: int, format: str = "md", db: Session = Depends(get_db)
):
    """预告片导出:md(拍摄手册)/ srt(字幕)。"""
    project = get_project_or_404(db, project_id)
    row = (
        db.query(DramaTrailer).filter(DramaTrailer.project_id == project_id).first()
    )
    if row is None or not row.shots:
        raise HTTPException(status_code=400, detail="还没生成预告片。")
    trailer = {"target_s": row.target_s, "title": row.title, "lines": row.lines or [],
               "shots": row.shots or [], "totals": {}}
    if format == "srt":
        content = export_trailer_srt(row.shots or [])
        media = "application/x-subrip; charset=utf-8"
        name = f"{project.title}-预告片.srt"
    else:
        content = export_trailer_markdown(project, trailer)
        media = "text/markdown; charset=utf-8"
        name = f"{project.title}-预告片-拍摄手册.md"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
