# app/api/project_io.py
# -*- coding: utf-8 -*-
"""项目级完整导出 / 导入 API。

导出:GET /api/projects/{id}/export → 下载 JSON 文件
导入:POST /api/projects/import → 上传 JSON 文件,创建为新项目
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Project, User
from app.db.session import get_db
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
