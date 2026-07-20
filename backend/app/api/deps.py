# app/api/deps.py
# -*- coding: utf-8 -*-
"""接口层公共工具:取项目并校验归属。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import assert_project_owner
from app.db.models import Project


def get_project_or_404(db: Session, project_id: int) -> Project:
    """取项目:不存在 → 404;不属于当前用户 → 404(不泄露存在性)。"""
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(p)
    return p
