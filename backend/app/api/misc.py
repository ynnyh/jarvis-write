# app/api/misc.py
# -*- coding: utf-8 -*-
"""杂项接口:任务进度 / Token 用量 / 导出。

GET /api/jobs/{job_id}                任务进度(异步生成轮询)
GET /api/usage                        Token 用量汇总
GET /api/projects/{id}/export/txt     整本导出 txt
GET /api/projects/{id}/export/epub    整本导出 epub
"""
from __future__ import annotations

import io
import zipfile
from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, current_user_id, get_current_user
from app.db.models import Chapter, LlmUsage, Outline, Project
from app.db.session import get_db
from app.jobs import get_job

router = APIRouter(tags=["misc"], dependencies=[Depends(get_current_user)])


@router.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = get_job(job_id)
    # 归属校验:非本人的任务按"不存在"处理,不泄露 job 存在性
    if job is None or job.get("owner_id") != current_user_id.get():
        raise HTTPException(status_code=404, detail="任务不存在或已被清理")
    job.pop("owner_id", None)  # 内部字段,不下发
    return job


@router.get("/api/usage")
async def usage_summary(db: Session = Depends(get_db)):
    """Token 用量汇总(总量 + 按模型)——只统计当前用户。"""
    uid = current_user_id.get()
    rows = (
        db.query(
            LlmUsage.model,
            func.count(LlmUsage.id),
            func.sum(LlmUsage.prompt_tokens),
            func.sum(LlmUsage.completion_tokens),
        )
        .filter(LlmUsage.user_id == uid)
        .group_by(LlmUsage.model)
        .all()
    )
    by_model = [
        {
            "model": m, "calls": c,
            "prompt_tokens": int(p or 0), "completion_tokens": int(o or 0),
        }
        for m, c, p, o in rows
    ]
    return {
        "total_calls": sum(x["calls"] for x in by_model),
        "total_prompt_tokens": sum(x["prompt_tokens"] for x in by_model),
        "total_completion_tokens": sum(x["completion_tokens"] for x in by_model),
        "by_model": by_model,
    }


def _book(db: Session, project_id: int) -> tuple[Project, list[tuple[str, str]]]:
    """取书名与 [(章标题, 正文)],按章号排序,只含有正文的章。"""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_project_owner(project)
    titles = {
        o.chapter_number: o.title
        for o in db.query(Outline).filter(Outline.project_id == project_id)
    }
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    if not chapters:
        raise HTTPException(status_code=400, detail="还没有已定稿的章节")
    items = [
        (f"第{c.chapter_number}章 {titles.get(c.chapter_number, '')}".strip(), c.final_content)
        for c in chapters
    ]
    return project, items


@router.get("/api/projects/{project_id}/export/txt")
async def export_txt(project_id: int, db: Session = Depends(get_db)):
    project, items = _book(db, project_id)
    parts = [f"《{project.title}》\n"]
    for title, text in items:
        parts.append(f"\n\n{title}\n\n{text}")
    data = "\n".join(parts).encode("utf-8")
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{project.id}.txt"
        },
    )


@router.get("/api/projects/{project_id}/export/epub")
async def export_epub(project_id: int, db: Session = Depends(get_db)):
    """最小可用 epub(纯标准库 zip 打包,无外部依赖)。"""
    project, items = _book(db, project_id)

    def xhtml(title: str, body: str) -> str:
        paras = "".join(
            f"<p>{escape(p.strip())}</p>" for p in body.splitlines() if p.strip()
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            f"<title>{escape(title)}</title></head><body>"
            f"<h2>{escape(title)}</h2>{paras}</body></html>"
        )

    manifest, spine, files = [], [], []
    for i, (title, text) in enumerate(items, 1):
        fn = f"chap{i}.xhtml"
        manifest.append(
            f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="c{i}"/>')
        files.append((fn, xhtml(title, text)))

    nav_lis = "".join(
        f'<li><a href="chap{i}.xhtml">{escape(t)}</a></li>'
        for i, (t, _) in enumerate(items, 1)
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><title>目录</title></head><body><nav epub:type="toc"><ol>'
        + nav_lis + "</ol></nav></body></html>"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="uid">jarvis-write-{project.id}</dc:identifier>'
        f"<dc:title>{escape(project.title)}</dc:title>"
        "<dc:language>zh-CN</dc:language>"
        '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        "</metadata><manifest>"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        + "".join(manifest)
        + "</manifest><spine>" + "".join(spine) + "</spine></package>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        for fn, content in files:
            z.writestr(f"OEBPS/{fn}", content)

    return Response(
        content=buf.getvalue(),
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{project.id}.epub"
        },
    )
