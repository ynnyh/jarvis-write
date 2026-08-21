# app/api/chapters/extraction.py
# -*- coding: utf-8 -*-
"""手改/契约有误后的同步:重抽取圣经+下游摘要、契约重提+门禁重检。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.db.models import Chapter, ChapterIssue, Project
from app.db.session import SessionLocal, get_db
from app.jobs import create_job, fail_job, finish_job, fire_and_track, list_running, normalize_job_error, update_stage

from ._common import _db_locked, _get_chapter_or_404

logger = logging.getLogger("jarvis-write.chapters")

router = APIRouter()


@router.post("/{chapter_number}/re-extract-async")
async def re_extract_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """手改正文后:重抽取(幂等,先清旧账)→ 重建下游摘要。"""
    get_project_or_404(db, project_id)
    # 同章同步任务已在跑 → 复用,不重复起
    for jid, job in list_running(f"re-extract-{project_id}-"):
        if job["kind"] == f"re-extract-{project_id}-{chapter_number}":
            return {"job_id": jid}
    job_id = create_job(f"re-extract-{project_id}-{chapter_number}")

    async def runner() -> None:
        from app.engines.consistency.extractor import extract_and_apply
        from app.engines.pipeline.chapter import rebuild_summaries_after

        # 同步要跨多轮 LLM 调用,期间用量记账等在别的连接提交,会让本连接的读快照过期,
        # 升级写锁时撞 SQLITE_BUSY(WAL 下不走 busy_timeout)。两步都幂等(抽取先清旧账 /
        # 摘要覆盖写),故除尽量缩短事务外,再遇锁整体回滚重试兜底。
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            session = SessionLocal()
            try:
                project = session.get(Project, project_id)
                ch = (
                    session.query(Chapter)
                    .filter(
                        Chapter.project_id == project_id,
                        Chapter.chapter_number == chapter_number,
                    )
                    .first()
                )
                content = ch.final_content
                # 先结束读事务:别让初始读取的快照跨过下面的 LLM 调用
                session.commit()
                update_stage(job_id, "1/2 重新抽取状态(清旧账)")
                stats = await extract_and_apply(
                    session, project_id, chapter_number, content
                )
                # 抽取写入立刻提交:别拿着写锁跨下游摘要的多轮 LLM 调用
                session.commit()
                update_stage(job_id, "2/2 重建下游前情摘要")
                rebuilt = await rebuild_summaries_after(
                    session, project, chapter_number,
                    progress=lambda s: update_stage(job_id, f"2/2 {s}"),
                )
                session.commit()
                finish_job(job_id, {"extraction_stats": stats, "summaries_rebuilt": rebuilt})
                return
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                if _db_locked(exc) and attempt < max_attempts:
                    wait = min(2 ** attempt, 15)
                    logger.warning(
                        "re-extract(%s-%s)第 %d 次遇数据库锁,%ss 后重试: %s",
                        project_id, chapter_number, attempt, wait, exc,
                    )
                    update_stage(job_id, f"数据库忙,{wait}s 后重试({attempt}/{max_attempts})")
                    await asyncio.sleep(wait)
                    continue
                fail_job(job_id, normalize_job_error(exc)[:500])
                return
            finally:
                session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


@router.post("/{chapter_number}/contract-reextract-async")
async def contract_reextract_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """契约有误一键重提(docs/08 §8):重提上一章与本章的章末契约 + 本章门禁重检。

    场景:契约提取错了会导致本章门禁误报(对照的是上一章契约)或下一章
    衔接错位(注入的是本章契约)。重提两章契约后按当前正文重跑一致性检查,
    gate 来源问题清单幂等重建。

    自动放行:若本章原为 quarantined,重检后已无致命矛盾(常见于用户去故事
    圣经改掉了冲突设定),直接补走此前被跳过的章后链路(圣经抽取 / 文风备忘 /
    下游摘要)并回到「待审」,免去用户再单独理解「放行」。结果带 auto_released
    供前端提示。仍有致命矛盾则只更新清单、维持 quarantined(交前端引导继续处理)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    if not ch.final_content.strip():
        raise HTTPException(status_code=400, detail="本章尚无定稿正文,无法提取契约")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再重提。",
        )
    for jid, job in list_running(f"contract-{project_id}-"):
        if job["kind"] == f"contract-{project_id}-{chapter_number}":
            return {"job_id": jid}
    job_id = create_job(f"contract-{project_id}-{chapter_number}")

    async def runner() -> None:
        from app.engines.common import get_outline
        from app.engines.consistency.checker import (
            blockers_of,
            check_chapter,
            persist_issues,
        )
        from app.engines.pipeline.chapter import (
            _rolling_summary,
            apply_chapter_tail,
            rebuild_summaries_after,
            update_style_memo,
        )
        from app.engines.pipeline.handoff import (
            extract_handoff_contract,
            handoff_payload,
        )
        from app.llm.router import Task, get_adapter_for

        session = SessionLocal()
        try:
            ch2 = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
                .first()
            )
            content = ch2.final_content
            prev = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number - 1,
                )
                .first()
            )
            has_prev = bool(prev and prev.final_content.strip())
            total_steps = 3 if has_prev else 2
            session.commit()  # 结束读事务,不拿快照跨 LLM 调用
            adapter = get_adapter_for(Task.HANDOFF_EXTRACT)
            step = 0
            if has_prev:
                step += 1
                update_stage(job_id, f"{step}/{total_steps} 重提上一章(第 {chapter_number - 1} 章)契约")
                await extract_handoff_contract(
                    session, prev, chapter_number - 1, prev.final_content, adapter
                )
            step += 1
            update_stage(job_id, f"{step}/{total_steps} 重提本章契约")
            await extract_handoff_contract(
                session, ch2, chapter_number, content, adapter
            )
            step += 1
            update_stage(job_id, f"{step}/{total_steps} 一致性门禁重检")
            issues = await check_chapter(
                session, project_id, chapter_number, content,
                rolling_summary=_rolling_summary(session, project_id, chapter_number),
            )
            persist_issues(session, ch2, issues, source="gate", text=content)
            session.commit()
            # 重检干净(0 致命矛盾)且原本被拦截 → 自动补走被跳过的章后链路并回到「待审」,
            # 免去用户再单独理解「放行 / 契约重提」。open 的非致命问题一并转 ignored
            # (与 gate-release 同语义)。任一步抛错 → 外层 except 整体回滚、维持 quarantined。
            blocker_list = blockers_of(issues)
            auto_released = False
            if not blocker_list and ch2.status == "quarantined":
                project = session.get(Project, project_id)
                session.query(ChapterIssue).filter(
                    ChapterIssue.chapter_id == ch2.id,
                    ChapterIssue.status == "open",
                ).update({"status": "ignored"}, synchronize_session=False)
                ch2.status = "pending_review"
                session.commit()
                update_stage(job_id, "设定已无冲突,自动补走圣经/摘要(自动放行)")
                outline = get_outline(session, project_id, chapter_number)
                await apply_chapter_tail(
                    session, project, ch2, chapter_number, content,
                    outline.title if outline else "",
                    report=lambda s: update_stage(job_id, f"自动放行 {s}"),
                )
                await update_style_memo(session, project, chapter_number, content)
                await rebuild_summaries_after(
                    session, project, chapter_number,
                    progress=lambda s: update_stage(job_id, f"自动放行 {s}"),
                )
                session.commit()
                auto_released = True
            handoff = handoff_payload(session, ch2)
            finish_job(job_id, {
                "contract_status": handoff["status"],
                "contract_error": handoff["error"],
                "issues": len(issues),
                "blockers": len(blocker_list),
                "auto_released": auto_released,
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}
