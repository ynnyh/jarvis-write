# app/api/refresh.py
# -*- coding: utf-8 -*-
"""重构翻新接口:让已有书按新生成逻辑(beats/文风备忘/去AI味)翻新。

四个异步任务(都返回 job_id,前端轮询 /api/jobs/{id}):
- backfill-beats   为已有大纲回填场景节拍(重度翻新的结构基础)
- seed-style-memo  扫已有正文生成初始文风备忘(翻新前先有"声音基准")
- light            轻度重润:锁情节 + 新文风约束润一遍(不改剧情)
- heavy            重度重写:带 beats/concept/文风备忘重跑 generate_chapter
                   (自带重抽圣经 + 重建下游摘要)

重度翻新会写章节正文,与逐章生成/连写队列互斥(复用 chapter-{pid}- 前缀锁)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.refresh import (
    backfill_beats,
    light_refresh_chapter,
    seed_style_memo,
)
from app.jobs import create_job, fail_job, finish_job, list_running, normalize_job_error, update_stage

logger = logging.getLogger("jarvis-write.refresh")

router = APIRouter(
    prefix="/api/projects/{project_id}/refresh",
    tags=["refresh"],
    dependencies=[Depends(get_current_user)],
)


class ChapterSelection(BaseModel):
    # 空列表 = 全书(由后端展开为所有已有章节/大纲)
    chapter_numbers: list[int] = Field(default_factory=list)
    # 用户的批量修改要求(可选):轻度重润注入润色 prompt,重度重写作为重写意见
    directive: str = Field(default="", max_length=500)


def _all_outline_numbers(db: Session, project_id: int) -> list[int]:
    return sorted(
        o.chapter_number
        for o in db.query(Outline.chapter_number).filter(
            Outline.project_id == project_id
        )
    )


def _all_written_numbers(db: Session, project_id: int) -> list[int]:
    return sorted(
        c.chapter_number
        for c in db.query(Chapter.chapter_number).filter(
            Chapter.project_id == project_id,
            Chapter.final_content != "",
        )
    )


def _chapter_job_busy(project_id: int) -> str | None:
    """章节写任务(生成/队列/同步/翻新)是否在跑;返回占用者 stage 或 None。"""
    busy = list_running(f"chapter-{project_id}-") + list_running(
        f"re-extract-{project_id}-"
    ) + list_running(f"refresh-{project_id}-")
    return busy[0][1]["stage"] if busy else None


# ---------------- 1. 回填节拍 ----------------

@router.post("/backfill-beats")
async def backfill_beats_async(
    project_id: int, req: ChapterSelection, db: Session = Depends(get_db)
):
    """为大纲回填场景节拍(只写 outline.beats,不动正文/圣经)。"""
    get_project_or_404(db, project_id)
    nums = req.chapter_numbers or _all_outline_numbers(db, project_id)
    if not nums:
        raise HTTPException(status_code=400, detail="没有可回填的大纲,请先生成蓝图。")
    # 与所有章节写任务(生成/队列/同步/翻新)互斥:回填写 outline.beats,与重度
    # 重写并发会撞 outline 写锁;同名任务已在跑时也借此天然去重(refresh- 前缀涵盖)。
    busy = _chapter_job_busy(project_id)
    if busy:
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")
    job_id = create_job(f"refresh-{project_id}-beats")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            result = await backfill_beats(session, project, nums, progress=progress)
            session.commit()
            return result
        finally:
            session.close()

    _spawn(job_id, work)
    return {"job_id": job_id}


# ---------------- 2. 初始化文风备忘 ----------------

@router.post("/seed-style-memo")
async def seed_style_memo_async(
    project_id: int, db: Session = Depends(get_db)
):
    """扫已有正文生成初始文风备忘(已有备忘则跳过,不覆盖)。"""
    get_project_or_404(db, project_id)
    # 与其他章节/翻新任务互斥,并借 refresh- 前缀天然去重(防重复点击起多个扫描 job)。
    busy = _chapter_job_busy(project_id)
    if busy:
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")
    job_id = create_job(f"refresh-{project_id}-memo")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            had_memo = bool((project.style_memo or "").strip())
            memo = await seed_style_memo(session, project, progress=progress)
            session.commit()
            # existed=已有未覆盖;seeded=本次新生成;皆否=无正文可扫或生成失败
            return {
                "style_memo": memo or "",
                "seeded": bool(memo) and not had_memo,
                "existed": had_memo,
            }
        finally:
            session.close()

    _spawn(job_id, work)
    return {"job_id": job_id}


# ---------------- 3. 轻度重润(批量) ----------------

@router.post("/light")
async def light_refresh_async(
    project_id: int, req: ChapterSelection, db: Session = Depends(get_db)
):
    """轻度重润:锁情节去AI味,批量按章排队。不改剧情,不重抽圣经。

    directive: 用户的批量修改要求(如"对话太书面化"),注入每章润色 prompt,
    仍受锁情节铁律约束(只动文笔,不改剧情)。
    """
    get_project_or_404(db, project_id)
    nums = req.chapter_numbers or _all_written_numbers(db, project_id)
    if not nums:
        raise HTTPException(status_code=400, detail="没有已成文的章节可重润。")
    directive = req.directive.strip()
    busy = _chapter_job_busy(project_id)
    if busy:
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")
    job_id = create_job(f"refresh-{project_id}-light")

    async def work(progress) -> dict:
        done: list[int] = []
        failed: list[dict] = []
        total = len(nums)
        for i, n in enumerate(nums, 1):
            session = SessionLocal()
            try:
                progress(f"[{i}/{total}] 第 {n} 章:重润")
                project = session.get(Project, project_id)
                await light_refresh_chapter(session, project, n, directive=directive)
                session.commit()
                done.append(n)
            except Exception as exc:  # noqa: BLE001 — 单章失败不中断整批
                session.rollback()
                failed.append({"chapter": n, "error": normalize_job_error(exc)[:200]})
            finally:
                session.close()
        return {"refreshed": done, "failed": failed, "total": total}

    _spawn(job_id, work)
    return {"job_id": job_id}


# ---------------- 4. 重度重写(批量) ----------------

@router.post("/heavy")
async def heavy_refresh_async(
    project_id: int, req: ChapterSelection, db: Session = Depends(get_db)
):
    """重度重写:带 beats/concept/文风备忘重跑 generate_chapter(自带重抽+重建摘要)。

    与逐章生成/连写队列互斥。按章号顺序串行(摘要链依赖顺序)。
    单章失败即停,但已完成的章各自提交、进度不丢;结果带 remaining(剩余章),
    前端可据此"续跑"而不必从头再来。
    directive: 用户的批量修改要求,作为重写意见传给每章生成。
    """
    get_project_or_404(db, project_id)
    nums = sorted(set(req.chapter_numbers or _all_written_numbers(db, project_id)))
    directive = req.directive.strip()
    if not nums:
        raise HTTPException(status_code=400, detail="没有可重写的章节。")
    # 每章都得有大纲(generate_chapter 前置)
    have = {
        o.chapter_number
        for o in db.query(Outline.chapter_number).filter(Outline.project_id == project_id)
    }
    missing = [n for n in nums if n not in have]
    if missing:
        raise HTTPException(status_code=400, detail=f"第 {missing} 章没有大纲,无法重写。")
    busy = _chapter_job_busy(project_id)
    if busy:
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")
    job_id = create_job(f"refresh-{project_id}-heavy")

    async def work(progress) -> dict:
        from app.engines.pipeline.chapter import generate_chapter

        done: list[int] = []
        total = len(nums)
        for i, n in enumerate(nums, 1):
            session = SessionLocal()
            try:
                project = session.get(Project, project_id)
                await generate_chapter(
                    session, project, n,
                    progress=lambda s, _n=n, _i=i: progress(f"[{_i}/{total}] 第 {_n} 章:{s}"),
                    revision=directive or None,
                )
                session.commit()
                done.append(n)
            except Exception as exc:  # noqa: BLE001 — 断链即停(后续章依赖本章摘要)
                session.rollback()
                # 已完成的章都已各自提交,进度不丢;返回剩余章供前端"续跑"
                logger.warning("重度重写中断于第 %d 章: %s", n, exc, exc_info=True)
                return {
                    "rewritten": done,
                    "total": total,
                    "stopped_at": n,
                    "remaining": nums[i - 1:],
                    "error": f"第 {n} 章重写失败:{normalize_job_error(exc)[:300]}",
                }
            finally:
                session.close()
        return {
            "rewritten": done,
            "total": total,
            "stopped_at": None,
            "remaining": [],
            "error": None,
        }

    _spawn(job_id, work)
    return {"job_id": job_id}


def _spawn(job_id: str, work) -> None:
    """把 work(progress) 挂到后台,结果/异常落到已建好的 job。

    与 jobs.spawn_job 等价,但复用外部已 create_job 的 job_id(便于返回后立即可轮询)。
    """
    import asyncio

    async def runner() -> None:
        try:
            result = await work(lambda s: update_stage(job_id, s))
            finish_job(job_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("翻新任务 %s 失败: %s", job_id, exc, exc_info=True)
            fail_job(job_id, normalize_job_error(exc)[:500])

    asyncio.create_task(runner())
