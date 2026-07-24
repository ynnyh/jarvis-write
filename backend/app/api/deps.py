# app/api/deps.py
# -*- coding: utf-8 -*-
"""接口层公共工具:取项目并校验归属;项目级联删除(用户删除时复用)。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import assert_project_owner
from app.db.models import (
    Architecture,
    Chapter,
    ChapterSummary,
    Entity,
    Fact,
    Foreshadowing,
    KnowledgeState,
    Outline,
    OutlineVersion,
    Project,
    Relationship,
)


def get_project_or_404(db: Session, project_id: int) -> Project:
    """取项目:不存在 → 404;不属于当前用户 → 404(不泄露存在性)。"""
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(p)
    return p


def delete_project_cascade(db: Session, project: Project) -> int:
    """删除项目及其全部关联数据(大纲/正文/摘要/事实库/伏笔等),不可恢复。

    模型只在 architecture 上配了 ORM cascade,且 SQLite 默认不开外键约束,
    因此逐表显式删除;llm_usage 无 project_id(按用户记账),不在清理范围。
    项目接口的删除与后台删用户都走这里。返回删除的章节数。
    """
    project_id = project.id

    outline_ids = [
        row.id
        for row in db.query(Outline.id).filter(Outline.project_id == project_id)
    ]
    if outline_ids:
        db.query(OutlineVersion).filter(
            OutlineVersion.outline_id.in_(outline_ids)
        ).delete(synchronize_session=False)
    deleted_chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .delete(synchronize_session=False)
    )
    for model in (
        ChapterSummary,
        KnowledgeState,
        Fact,
        Relationship,
        Entity,
        Foreshadowing,
        Outline,
        Architecture,
    ):
        db.query(model).filter(model.project_id == project_id).delete(
            synchronize_session=False
        )
    db.delete(project)
    db.commit()

    return deleted_chapters
