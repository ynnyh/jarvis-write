# app/api/project_io.py
# -*- coding: utf-8 -*-
"""项目级完整导出 / 导入 API。

导出:GET /api/projects/{id}/export → 下载 JSON 文件
导入:POST /api/projects/import → 上传 JSON 文件,创建为新项目
导入:POST /api/projects/import-book → 上传 TXT/DOCX 整本旧书,解析章节建为新项目
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Chapter, Project, User
from app.db.session import get_db
from app.engines.book_import import (
    MAX_IMPORT_BYTES,
    decode_text,
    extract_docx_text,
    import_book_to_project,
)
from app.engines.project_io import export_project_to_json, import_project_from_json

logger = logging.getLogger("jarvis-write.project_io")

router = APIRouter(
    prefix="/api/projects",
    tags=["project-io"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/{project_id}/export")
async def export_project_endpoint(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """导出指定项目的全部数据为 JSON 文件。

    包含:项目基本信息、架构、大纲+版本、章节+版本+状态+问题+摘要、
    故事圣经(实体/事实/关系/知识状态)、伏笔、写作卡、倾向预设。

    不包含:用户信息、LLM key、其他工坊数据。
    """
    # 归属校验
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        json_str = export_project_to_json(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 文件名用项目标题,去掉不安全字符
    safe_title = "".join(c for c in project.title if c.isalnum() or c in " -_") or "project"
    filename = f"{safe_title}-jarvis-write-export.json"

    return Response(
        content=json_str,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.post("/import")
async def import_project_endpoint(
    file: UploadFile = File(...),
    title: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从导出的 JSON 文件导入为新项目。

    Args:
        file: 上传的 JSON 文件(由 /api/projects/{id}/export 导出)
        title: 可选,覆盖新项目标题(默认用原标题 + " (导入)")

    Returns:
        新项目的 id 和标题
    """
    # 读取文件内容
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"文件格式错误,不是有效的 JSON: {e}") from e

    try:
        project = import_project_from_json(db, json.dumps(data), current_user.id, title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("导入项目失败")
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e

    return {
        "project_id": project.id,
        "title": project.title,
        "message": "项目导入成功",
    }


@router.post("/import-book")
async def import_book_endpoint(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """导入整本旧书(TXT / DOCX):解析分卷/章节,建为可继续写作的新项目。

    每章落一条大纲 + 一条 approved 正文(作者成稿直接计入总字数,可检索、
    可翻新、可进阅读器)。无章标题时按段落边界按长度兜底切章。
    """
    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 20MB 上限,请拆分后分卷导入")

    name = (file.filename or "book.txt").strip()
    if name.lower().endswith(".docx"):
        try:
            text = extract_docx_text(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif name.lower().endswith(".txt") or name.lower().endswith(".text"):
        text = decode_text(raw)
    else:
        raise HTTPException(status_code=400, detail="只支持 .txt / .docx 文件")

    if not text.strip():
        raise HTTPException(status_code=400, detail="文件里没有可导入的文本内容")

    try:
        project = import_book_to_project(
            db, current_user.id, name, text, title_override=title or None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("整本导入失败: %s", name)
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e

    chapter_count = db.query(Chapter).filter(Chapter.project_id == project.id).count()
    return {
        "project_id": project.id,
        "title": project.title,
        "chapters": chapter_count,
        "message": "导入成功",
    }
