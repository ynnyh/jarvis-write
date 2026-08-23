# app/api/deps.py
# -*- coding: utf-8 -*-
"""接口层公共工具:取项目并校验归属;项目级联删除(用户删除时复用)。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import storage
from app.auth import assert_project_owner
from app.db.models import (
    Architecture,
    Chapter,
    ChapterSummary,
    DramaCharacterCard,
    DramaEpisode,
    DramaProductionPack,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    DramaTrailer,
    Entity,
    Fact,
    Foreshadowing,
    KnowledgeState,
    Outline,
    OutlineVersion,
    Project,
    Relationship,
    WritingCard,
)


def get_project_or_404(db: Session, project_id: int) -> Project:
    """取项目:不存在 → 404;不属于当前用户 → 404(不泄露存在性)。"""
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(p)
    return p


def delete_project_cascade(db: Session, project: Project) -> int:
    """删除项目及其全部关联数据(大纲/正文/摘要/事实库/伏笔/漫剧资产),不可恢复。

    模型只在 architecture 上配了 ORM cascade,且 SQLite 默认不开外键约束,
    因此逐表显式删除;llm_usage 无 project_id(按用户记账),不在清理范围。
    漫剧的分镜与成片包挂在集上(episode_id),要先按集号删再删集本身。
    上传的定妆照文件在数据库提交之后清理——顺序反了会出现「文件删了库没删」。
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
    episode_ids = [
        row.id
        for row in db.query(DramaEpisode.id).filter(
            DramaEpisode.project_id == project_id
        )
    ]
    if episode_ids:
        for model in (DramaShot, DramaProductionPack):
            db.query(model).filter(model.episode_id.in_(episode_ids)).delete(
                synchronize_session=False
            )
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
        WritingCard,
        DramaEpisode,
        DramaTrailer,
        DramaSceneCard,
        DramaCharacterCard,
        DramaStyleCard,
        Outline,
        Architecture,
    ):
        db.query(model).filter(model.project_id == project_id).delete(
            synchronize_session=False
        )
    db.delete(project)
    db.commit()

    storage.delete_project_dir(project_id)

    return deleted_chapters
