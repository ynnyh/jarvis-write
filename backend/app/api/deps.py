# app/api/deps.py
# -*- coding: utf-8 -*-
"""接口层公共工具:取项目并校验归属;项目级联删除;回收站清理。"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger("jarvis-write.deps")

# 回收站保留期(天):软删的书留这么久供复盘,超期才物理清理。
# 与 jobs.JOB_RETENTION_DAYS 同风格(可环境变量覆盖;设 0 表示永不物理清理)。
PROJECT_TRASH_RETENTION_DAYS = max(
    0, int(os.environ.get("PROJECT_TRASH_RETENTION_DAYS", "30"))
)

from app import storage
from app.auth import assert_project_owner
from app.db.models import (
    Architecture,
    Chapter,
    ChapterIssue,
    ChapterState,
    ChapterSummary,
    ChapterVersion,
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
    RenderTask,
    WritingCard,
)


def get_project_or_404(db: Session, project_id: int) -> Project:
    """取项目:不存在 / 已软删 → 404;不属于当前用户 → 404(不泄露存在性)。

    软删(docs/29 留痕):用户点「删除」只是置 deleted_at,前台一律当它不存在;
    复盘与清理走 admin 通道(直读库,不经这里)。
    """
    p = db.get(Project, project_id)
    if p is None or p.deleted_at is not None:
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
    # 出片任务与渲染草片跟着项目走:先收文件名,行删掉后再清文件(与上传图同序)
    from app.api.render import purge_render_tasks

    render_files = [
        t.result_path
        for t in db.query(RenderTask).filter(RenderTask.project_id == project_id)
        if t.result_path
    ]
    purge_render_tasks(db, project_id=project_id)
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
    for rel in render_files:
        storage.delete_render_file(rel)

    return deleted_chapters


def reset_project_content(db: Session, project: Project) -> int:
    """清空「已写正文与大纲」及以旧正文为源抽取的派生数据,可据此从新架构重来。

    保留:架构 / 概念 / DNA / 宪法 / 简介 / 全局倾向 / 手法卡 / 卷纲 / 文风备忘。
    这些是创作期的「上流输入」,架构重写后仍有参考价值;真正对不上的是正文与大纲。
    派生数据(摘要 / 事实账本 / 伏笔 / 章节契约 / 正文历史)都由旧正文抽取,一并清掉,
    避免残留旧设定误导后续生成。状态复位到 outlining(等用户重铺蓝图)。返回删的章节数。
    """
    project_id = project.id

    chapter_ids = [
        row.id
        for row in db.query(Chapter.id).filter(Chapter.project_id == project_id)
    ]
    if chapter_ids:
        # 这些表按 chapter_id 挂靠;SQLite 默认不开外键,先删引用再删章本体
        for model in (ChapterVersion, ChapterState, ChapterIssue):
            db.query(model).filter(model.chapter_id.in_(chapter_ids)).delete(
                synchronize_session=False
            )
    outline_ids = [
        row.id
        for row in db.query(Outline.id).filter(Outline.project_id == project_id)
    ]
    if outline_ids:
        db.query(OutlineVersion).filter(
            OutlineVersion.outline_id.in_(outline_ids)
        ).delete(synchronize_session=False)

    db.query(ChapterSummary).filter(
        ChapterSummary.project_id == project_id
    ).delete(synchronize_session=False)
    for model in (
        KnowledgeState,
        Fact,
        Relationship,
        Entity,
        Foreshadowing,
        Outline,
        Chapter,
    ):
        db.query(model).filter(model.project_id == project_id).delete(
            synchronize_session=False
        )

    project.status = "outlining"
    project.outline_stale = False
    db.commit()
    return len(chapter_ids)


def release_project_files(db: Session, project: Project) -> None:
    """软删时立刻释放磁盘占用:配额不能被回收站里的书长期占着。

    留痕要留的是**文本证据**(作者输入/任务/用量/正文),定妆照与渲染草片是配额
    大头、没有复盘价值,所以在软删当时就删;数据库行留到保留期满再物理清。
    """
    from app.api.render import release_render_files

    project_id = project.id
    # 渲染草片 + 末帧(约定落位只在 render.release_render_files 里写一次)
    release_render_files(db, project_id=project_id)
    storage.delete_project_dir(project_id)


def purge_deleted_projects(db: Session | None = None) -> int:
    """物理清理软删超期的项目(服务启动时调用),返回清理条数。

    软删只解决「前台不可见」,库里的数据仍占空间;保留期一到就走老的级联删除。
    清理失败不阻塞启动(与 jobs.purge_old_jobs 同纪律)。
    """
    if PROJECT_TRASH_RETENTION_DAYS <= 0:
        return 0
    from datetime import datetime, timedelta, timezone

    from app.db.session import SessionLocal

    own = db is None
    session = SessionLocal() if own else db
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=PROJECT_TRASH_RETENTION_DAYS)
        stale = (
            session.query(Project)
            .filter(Project.deleted_at.isnot(None))
            .filter(Project.deleted_at < cutoff)
            .all()
        )
        n = 0
        for p in stale:
            delete_project_cascade(session, p)  # 内部自行 commit
            n += 1
        if n:
            logger.info("回收站清理:物理删除 %d 个超期软删项目", n)
        return n
    except Exception:  # noqa: BLE001 — 清理失败不阻塞启动
        logger.warning("回收站清理失败", exc_info=True)
        return 0
    finally:
        if own:
            session.close()
