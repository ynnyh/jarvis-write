# app/api/chapters/generation.py
# -*- coding: utf-8 -*-
"""章节生成:单章生成(同步/异步)与连写队列。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.db.models import Chapter, Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.chapter import generate_chapter
from app.engines.pipeline.handoff import handoff_payload
from app.jobs import create_job, fail_job, finish_job, fire_and_track, list_running, normalize_job_error, update_stage
from app.schemas.tendency import Tendency

from ._common import ChapterDetail, _fill_handoff, _flavor_dict, _gate_payload

router = APIRouter()


class GenerateChapterRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)
    # 重写时的修改意见(可选,最长 500 字;首次生成传了也会被引擎忽略)
    revision: str = Field(default="", max_length=500)


class GenerateChapterResponse(ChapterDetail):
    """生成结果:正文 + 一致性检查问题 + 圣经抽取统计 + AI 味指数 + 字数守卫结果 + 审校把关结果。"""

    consistency_issues: list[dict] = []
    extraction_stats: dict = {}
    # AI 味指数:纯规则统计(不调 LLM,零额外耗时),生成完成即给出
    ai_flavor: dict = {}
    # 字数守卫:none / compressed / split
    word_guard_action: str = "none"
    split_info: dict = {}
    # 编辑部审校把关:scores(四维+continuity)/comment/suggestions/passed/
    # revision_rounds/threshold/repair_rounds/repairs(门禁定点修复明细)
    review: dict = {}
    # 一致性门禁结果(docs/08 §5.4):{"status": "passed"|"quarantined", "blockers": [...]}
    gate: dict = {}
    # 写前审核警告(docs/08 §5.3):{"warnings": [...]},severity 一律 major,只警告不阻断
    preflight: dict = {}


@router.post("/{chapter_number}/generate", response_model=GenerateChapterResponse)
async def generate(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """生成一章(草稿/定稿/检查/抽取/摘要,多次 LLM 调用,耗时较长)。"""
    project = get_project_or_404(db, project_id)
    try:
        chapter, issues, stats, guard_result, review_result, preflight = (
            await generate_chapter(
                db, project, chapter_number, req.tendency,
                revision=req.revision.strip(),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    resp = GenerateChapterResponse.model_validate(chapter, from_attributes=True)
    resp.consistency_issues = issues
    resp.extraction_stats = stats
    resp.ai_flavor = _flavor_dict(chapter.final_content)
    resp.word_guard_action = guard_result.action
    resp.split_info = guard_result.split_info
    resp.review = review_result
    resp.gate = _gate_payload(chapter, issues)
    resp.preflight = {"warnings": preflight}
    _fill_handoff(db, chapter, resp)
    return resp


@router.post("/{chapter_number}/generate-async")
async def generate_async(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """异步生成:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看五段进度。"""
    get_project_or_404(db, project_id)  # 先校验存在
    # 防重复提交:同一项目同时只跑一个章节任务(生成/队列/一致性同步)。
    # 同章已在生成 → 直接复用该任务(前端接上轮询);他章/队列在跑 → 明确拒绝。
    for jid, job in list_running(f"chapter-{project_id}-") + list_running(f"re-extract-{project_id}-"):
        tail = job["kind"].rsplit("-", 1)[1]
        if not tail.isdigit():
            raise HTTPException(
                status_code=409,
                detail=f"连写队列还在进行中({job['stage']}),请等它完成再单独生成。",
            )
        running_num = int(tail)
        if job["kind"].startswith("chapter-") and running_num == chapter_number:
            return {"job_id": jid}
        raise HTTPException(
            status_code=409,
            detail=f"第 {running_num} 章的任务还在进行中({job['stage']}),请等它完成再发起新的生成。",
        )
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            chapter, issues, stats, guard_result, review_result, preflight = (
                await generate_chapter(
                    session, project, chapter_number, req.tendency,
                    progress=lambda s: update_stage(job_id, s),
                    revision=req.revision.strip(),
                )
            )
            session.commit()
            handoff = handoff_payload(session, chapter)
            finish_job(job_id, {
                "chapter_number": chapter.chapter_number,
                "word_count": chapter.word_count,
                "status": chapter.status,
                "final_content": chapter.final_content,
                "draft_content": chapter.draft_content,
                "is_stale": chapter.is_stale,
                "outline_version_used": chapter.outline_version_used,
                "consistency_issues": issues,
                "extraction_stats": stats,
                "ai_flavor": _flavor_dict(chapter.final_content),
                "word_guard_action": guard_result.action,
                "split_info": guard_result.split_info,
                "review": review_result,
                "gate": _gate_payload(chapter, issues),
                "preflight": {"warnings": preflight},
                "handoff_contract": handoff["contract"],
                "handoff_extract_status": handoff["status"],
                "handoff_extract_error": handoff["error"],
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


class GenerateQueueRequest(BaseModel):
    chapter_numbers: list[int] = Field(min_length=1, max_length=50)
    tendency: dict = Field(default_factory=dict)


@router.post("/generate-queue")
async def generate_queue(
    project_id: int,
    req: GenerateQueueRequest,
    db: Session = Depends(get_db),
):
    """连写队列:勾选多章排队,后台按章号顺序串行生成(滚动摘要链依赖顺序)。

    一个 job 跑到底;某章失败即停止(后续章依赖它的前情摘要),已完成的章保留。
    """
    get_project_or_404(db, project_id)
    nums = sorted(set(req.chapter_numbers))
    # 校验:每章都得有蓝图
    have = {
        o.chapter_number
        for o in db.query(Outline.chapter_number).filter(Outline.project_id == project_id)
    }
    missing = [n for n in nums if n not in have]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"第 {missing} 章还没有大纲蓝图,先去「大纲」生成。"
        )
    # 互斥:项目下任何章节任务(单章/队列/同步)在跑都拒绝
    busy = list_running(f"chapter-{project_id}-") + list_running(f"re-extract-{project_id}-")
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再排队。",
        )
    job_id = create_job(f"chapter-{project_id}-queue")

    async def runner() -> None:
        completed: list[dict] = []
        total = len(nums)
        for i, n in enumerate(nums, 1):
            session = SessionLocal()
            try:
                project = session.get(Project, project_id)
                # 严格连写模式(docs/08 §5.5,queue_require_approved=True):
                # 下一章生成前要求上一章已人工审核通过(approved),否则队列暂停。
                # 宽松模式(默认)维持现状——仅 quarantined 暂停(见下方生成后判断)。
                if project.queue_require_approved and n > 1:
                    prev = (
                        session.query(Chapter)
                        .filter(
                            Chapter.project_id == project_id,
                            Chapter.chapter_number == n - 1,
                        )
                        .first()
                    )
                    if prev is not None and prev.final_content and prev.status != "approved":
                        done = "、".join(str(c["chapter_number"]) for c in completed) or "无"
                        fail_job(
                            job_id,
                            f"严格连写模式:第 {n - 1} 章尚未人工审核通过"
                            f"(当前状态:{prev.status}),已暂停(已完成:{done};"
                            "请先在章节列表审核通过该章,或关闭项目设置里的"
                            "「连写要求审核通过」后再继续)",
                        )
                        return
                chapter, issues, _stats, _guard, _review, _preflight = (
                    await generate_chapter(
                        session, project, n, req.tendency,
                        progress=lambda s, _n=n, _i=i: update_stage(
                            job_id, f"[{_i}/{total}] 第 {_n} 章:{s}"
                        ),
                    )
                )
                session.commit()
                # 一致性门禁拦截(quarantined):与"失败即停"同语义——该章未走章后
                # 抽取/摘要,后续章没有可靠的前情摘要可用,必须停下等人工处理。
                if chapter.status == "quarantined":
                    blockers = [i for i in issues if i.get("severity") == "blocker"]
                    desc = ";".join(
                        (i.get("description") or "")[:60] for i in blockers[:3]
                    ) or "详见该章问题清单"
                    done = "、".join(str(c["chapter_number"]) for c in completed) or "无"
                    fail_job(
                        job_id,
                        f"第 {n} 章被一致性门禁拦截(quarantined):{desc}"
                        f"(已完成:{done};该章未抽取进圣经、未更新摘要,已停止。"
                        "请重写该章或确认忽略放行后再继续连写)",
                    )
                    return
                completed.append({
                    "chapter_number": n, "word_count": chapter.word_count,
                })
            except Exception as exc:  # noqa: BLE001 — 断链即停,保留已完成
                session.rollback()
                done = "、".join(str(c["chapter_number"]) for c in completed) or "无"
                fail_job(
                    job_id,
                    f"第 {n} 章生成失败:{normalize_job_error(exc)[:300]}(已完成:{done};"
                    "后续章节依赖本章摘要,已停止)",
                )
                return
            finally:
                session.close()
        finish_job(job_id, {"completed": completed, "total": total})

    fire_and_track(runner())
    return {"job_id": job_id}
