# app/api/chapters.py
# -*- coding: utf-8 -*-
"""章节接口:逐章生成与查看。

POST /api/projects/{id}/chapters/{n}/generate   生成第 n 章(草稿→定稿→摘要→入库)
GET  /api/projects/{id}/chapters                章节列表(不含正文,轻量)
GET  /api/projects/{id}/chapters/{n}            单章详情(含正文)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, ChapterIssue, ChapterVersion, Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.common import get_outline
from app.engines.pipeline.chapter import (
    apply_chapter_tail,
    discuss_revision,
    generate_chapter,
    rebuild_summaries_after,
    update_style_memo,
)
from app.engines.pipeline.handoff import handoff_payload
from app.engines.polish import ai_flavor_report
from app.jobs import create_job, fail_job, finish_job, list_running, update_stage
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapters")


def _db_locked(exc: BaseException) -> bool:
    """是否 SQLite 写锁冲突(含 WAL 下旧快照升级写锁、不走 busy_timeout 的那种)。"""
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


router = APIRouter(
    prefix="/api/projects/{project_id}/chapters",
    tags=["chapters"],
    dependencies=[Depends(get_current_user)],
)


class GenerateChapterRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)
    # 重写时的修改意见(可选,最长 500 字;首次生成传了也会被引擎忽略)
    revision: str = Field(default="", max_length=500)


class ChapterBrief(BaseModel):
    chapter_number: int
    status: str
    word_count: int
    is_stale: bool

    model_config = {"from_attributes": True}


class ChapterDetail(ChapterBrief):
    draft_content: str
    final_content: str
    outline_version_used: int
    # 本章章末交接契约(docs/08 §5.2):无契约/正文已改动(指纹不符)时为 None;
    # status:none(从未提取)/ ok / failed(失败留痕,error 记原因,不阻塞生成)
    handoff_contract: dict | None = None
    handoff_extract_status: str = "none"
    handoff_extract_error: str = ""


def _fill_handoff(db: Session, chapter: Chapter, resp: ChapterDetail) -> None:
    """把本章契约填进响应(契约存在 chapter_states 表,不在 chapters 行上)。"""
    payload = handoff_payload(db, chapter)
    resp.handoff_contract = payload["contract"]
    resp.handoff_extract_status = payload["status"]
    resp.handoff_extract_error = payload["error"]


class GenerateChapterResponse(ChapterDetail):
    """生成结果:正文 + 一致性检查问题 + 圣经抽取统计 + AI 味指数 + 字数守卫结果 + 审校把关结果。"""

    consistency_issues: list[dict] = []
    extraction_stats: dict = {}
    # AI 味指数:纯规则统计(不调 LLM,零额外耗时),生成完成即给出
    ai_flavor: dict = {}
    # 字数守卫:none / compressed / split
    word_guard_action: str = "none"
    split_info: dict = {}
    # 编辑部审校把关:scores(四维+continuity)/comment/suggestions/passed/revision_rounds/threshold
    review: dict = {}
    # 一致性门禁结果(docs/08 §5.4):{"status": "passed"|"quarantined", "blockers": [...]}
    gate: dict = {}
    # 写前审核警告(docs/08 §5.3):{"warnings": [...]},severity 一律 major,只警告不阻断
    preflight: dict = {}


def _gate_payload(chapter: Chapter, issues: list[dict]) -> dict:
    """门禁结果透出:quarantined 状态 + blocker 列表(P1 前端审核面板对接用)。"""
    return {
        "status": "quarantined" if chapter.status == "quarantined" else "passed",
        "blockers": [i for i in issues if i.get("severity") == "blocker"],
    }


def _flavor_dict(text: str) -> dict:
    """生成结果卡的 AI 味字段:score/summary + 分类得分明细(hover 展示用)。"""
    report = ai_flavor_report(text)
    return {
        "score": report.score,
        "summary": report.summary(),
        "categories": report.categories,
    }


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
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


class ReviseDiscussRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


class ReviseDiscussResponse(BaseModel):
    reply: str
    directive: str = ""


def _blueprint_block(outline: Outline | None, n: int) -> str:
    """把本章蓝图渲染成研讨对话的上下文;无蓝图时给提示。"""
    if outline is None:
        return f"(第 {n} 章还没有大纲蓝图)"
    return (
        f"第{n}章《{outline.title}》\n"
        f"- 核心作用:{outline.chapter_purpose}\n"
        f"- 伏笔操作:{outline.foreshadowing}\n"
        f"- 本章简述:{outline.summary}"
    )


@router.post("/{chapter_number}/revise-discuss", response_model=ReviseDiscussResponse)
async def revise_discuss(
    project_id: int,
    chapter_number: int,
    req: ReviseDiscussRequest,
    db: Session = Depends(get_db),
):
    """就某一章的重写与作者多轮研讨:聊清"到底哪里不满意" → 蒸馏出修改意见。

    前端拿返回的 directive 回填重写文本框,确认后作为 revision 去 generate-async 重写。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚无定稿正文,先生成再重写")
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    try:
        result = await discuss_revision(
            req.messages,
            blueprint_block=_blueprint_block(outline, chapter_number),
            chapter_block=ch.final_content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviseDiscussResponse(**result)


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
                    f"第 {n} 章生成失败:{str(exc)[:300]}(已完成:{done};"
                    "后续章节依赖本章摘要,已停止)",
                )
                return
            finally:
                session.close()
        finish_job(job_id, {"completed": completed, "total": total})

    asyncio.create_task(runner())
    return {"job_id": job_id}


class EditContentRequest(BaseModel):
    final_content: str = Field(min_length=1)


@router.put("/{chapter_number}/content", response_model=ChapterDetail)
async def edit_content(
    project_id: int,
    chapter_number: int,
    req: EditContentRequest,
    db: Session = Depends(get_db),
):
    """手动编辑正文:立即保存。保存后请调 re-extract-async 同步圣经/摘要。"""
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")
    # 覆盖前留一版:手改后悔可回退到编辑前
    snapshot_chapter(db, ch, source="edited")
    ch.final_content = req.final_content.strip()
    ch.word_count = len(ch.final_content)
    # 手改后内容未经审校/人工审核,回到待审核(docs/08 §5.5)
    ch.status = "pending_review"
    db.commit()
    return ch


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
                fail_job(job_id, str(exc)[:500])
                return
            finally:
                session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.post("/{chapter_number}/contract-reextract-async")
async def contract_reextract_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """契约有误一键重提(docs/08 §8):重提上一章与本章的章末契约 + 本章门禁重检。

    场景:契约提取错了会导致本章门禁误报(对照的是上一章契约)或下一章
    衔接错位(注入的是本章契约)。重提两章契约后按当前正文重跑一致性检查,
    gate 来源问题清单幂等重建。只更新清单,不改章节状态:quarantined 章
    重检干净后仍需「放行」补走圣经/摘要链路(或重写)。
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
        from app.engines.consistency.checker import (
            blockers_of,
            check_chapter,
            persist_issues,
        )
        from app.engines.pipeline.chapter import _rolling_summary
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
            handoff = handoff_payload(session, ch2)
            finish_job(job_id, {
                "contract_status": handoff["status"],
                "contract_error": handoff["error"],
                "issues": len(issues),
                "blockers": len(blockers_of(issues)),
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


# ---------- 一致性门禁:问题清单与 quarantined 放行 ----------


class ChapterIssueOut(BaseModel):
    """chapter_issues 记录(docs/08 §5.7):门禁/预审/诊断产出的一致性问题。"""

    id: int
    source: str
    severity: str
    issue_type: str
    description: str
    evidence: str
    suggestion: str
    status: str
    created_at: str


@router.get("/{chapter_number}/issues", response_model=list[ChapterIssueOut])
async def list_issues(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """本章的一致性问题清单(最新在前,含 open/resolved/ignored 各状态)。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    rows = (
        db.query(ChapterIssue)
        .filter(ChapterIssue.chapter_id == ch.id)
        .order_by(ChapterIssue.id.desc())
        .all()
    )
    return [
        ChapterIssueOut(
            id=r.id, source=r.source, severity=r.severity, issue_type=r.issue_type,
            description=r.description, evidence=r.evidence, suggestion=r.suggestion,
            status=r.status, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


class IssuePatchRequest(BaseModel):
    """问题状态流转(docs/08 §5.5):open → resolved / ignored(单向)。"""

    status: str = Field(min_length=1)


def _issue_out(r: ChapterIssue) -> ChapterIssueOut:
    return ChapterIssueOut(
        id=r.id, source=r.source, severity=r.severity, issue_type=r.issue_type,
        description=r.description, evidence=r.evidence, suggestion=r.suggestion,
        status=r.status, created_at=r.created_at.isoformat(),
    )


def _get_issue_or_404(db: Session, chapter_id: int, issue_id: int) -> ChapterIssue:
    issue = (
        db.query(ChapterIssue)
        .filter(ChapterIssue.id == issue_id, ChapterIssue.chapter_id == chapter_id)
        .first()
    )
    if issue is None:
        raise HTTPException(status_code=404, detail="问题记录不存在")
    return issue


@router.patch("/{chapter_number}/issues/{issue_id}", response_model=ChapterIssueOut)
async def patch_issue(
    project_id: int, chapter_number: int, issue_id: int,
    req: IssuePatchRequest, db: Session = Depends(get_db),
):
    """单条问题状态流转:open → resolved(已人工改完)/ ignored(确认忽略)。

    单向流转:已是 resolved/ignored 的不可再改(ignored 的失效语义照旧——
    正文指纹变化后门禁重建会清除旧 ignored,同一矛盾重新报警)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if req.status not in ("resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status 只能是 resolved 或 ignored")
    if issue.status != "open":
        raise HTTPException(
            status_code=400, detail=f"该问题已是 {issue.status} 状态,不可再流转"
        )
    issue.status = req.status
    db.commit()
    return _issue_out(issue)


@router.post("/{chapter_number}/issues/{issue_id}/apply-revision")
async def apply_issue_revision(
    project_id: int, chapter_number: int, issue_id: int,
    db: Session = Depends(get_db),
):
    """采纳单条问题的修正建议:拼成修订指令走重写链路(异步 job)。

    简化取舍:修订发起(本端点受理)即把该 issue 标 resolved;重写后门禁会重跑
    并重建 open 集,同类问题若未消除会以新的 open 记录回来(不会漏报,但
    resolved 标记不代表"已验证消除");且该 resolved 记录的正文指纹随重写失效,
    门禁重建时按既有指纹语义清除(与 ignored 的失效语义一致)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if issue.status != "open":
        raise HTTPException(
            status_code=400, detail=f"该问题已是 {issue.status} 状态,无需再修订"
        )
    if not (issue.suggestion or issue.description).strip():
        raise HTTPException(status_code=400, detail="该问题没有可用的修正建议")
    if not ch.final_content.strip():
        raise HTTPException(status_code=400, detail="本章尚无定稿正文,无法按问题修订")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再修订。",
        )
    # 修订指令:问题点 + 证据 + 修正建议(走 generate_chapter 的 revision 通道)
    revision = (
        f"针对一致性问题的修订:\n问题:{issue.description}"
        + (f"\n证据:{issue.evidence}" if issue.evidence else "")
        + (f"\n修正建议:{issue.suggestion}" if issue.suggestion else "")
    )[:500]
    # 简化语义:发起即标 resolved(见 docstring 的取舍说明)
    issue.status = "resolved"
    db.commit()
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            chapter, issues, stats, guard_result, review_result, preflight = (
                await generate_chapter(
                    session, project, chapter_number, None,
                    progress=lambda s: update_stage(job_id, s),
                    revision=revision,
                )
            )
            session.commit()
            handoff = handoff_payload(session, chapter)
            finish_job(job_id, {
                "chapter_number": chapter.chapter_number,
                "applied_issue_id": issue_id,
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
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.post("/{chapter_number}/approve", response_model=ChapterDetail)
async def approve_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """人工审核通过(docs/08 §5.5):pending_review → approved。

    幂等:已 approved 重复调用返回 200。quarantined 不可 approve——
    需先 gate-release 放行或重写通过门禁(返回 400)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    if ch.status == "approved":
        return ch  # 幂等
    if ch.status == "quarantined":
        raise HTTPException(
            status_code=400,
            detail="该章被一致性门禁隔离(quarantined),请先放行或重写通过后再审核",
        )
    if ch.status != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"当前状态({ch.status})不能审核通过,仅待审核(pending_review)章节可 approve",
        )
    ch.status = "approved"
    db.commit()
    resp = ChapterDetail.model_validate(ch, from_attributes=True)
    _fill_handoff(db, ch, resp)
    return resp


@router.post("/{chapter_number}/gate-release")
async def gate_release(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """quarantined 放行:确认忽略全部 blocker,补走被跳过的章后链路。

    后台任务按序执行:open issues 标 ignored → 状态回 pending_review(待人工
    审核,docs/08 §5.5)→ 章后抽取(写圣经)→ 滚动摘要 → 章末契约 → 文风备忘 →
    重建下游摘要。
    任一步失败整体回滚,章节保持 quarantined(不会放出"半同步"状态)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    if ch.status != "quarantined":
        raise HTTPException(status_code=400, detail="该章不在隔离(quarantined)状态,无需放行")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再放行。",
        )
    job_id = create_job(f"gate-release-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            ch2 = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
                .first()
            )
            if ch2 is None or ch2.status != "quarantined":
                raise ValueError("该章不在隔离(quarantined)状态,无需放行")
            update_stage(job_id, "1/3 标记放行(issues 转 ignored,状态回 pending_review)")
            session.query(ChapterIssue).filter(
                ChapterIssue.chapter_id == ch2.id,
                ChapterIssue.status == "open",
            ).update({"status": "ignored"}, synchronize_session=False)
            ch2.status = "pending_review"
            session.commit()
            # 补走 quarantined 时跳过的章后链路(抽取/摘要/契约,与生成主流程同一实现)
            outline = get_outline(session, project_id, chapter_number)
            stats = await apply_chapter_tail(
                session, project, ch2, chapter_number, ch2.final_content,
                outline.title if outline else "",
                report=lambda s: update_stage(job_id, f"2/3 {s}"),
            )
            update_stage(job_id, "3/3 文风备忘与下游摘要重建")
            await update_style_memo(session, project, chapter_number, ch2.final_content)
            rebuilt = await rebuild_summaries_after(
                session, project, chapter_number,
                progress=lambda s: update_stage(job_id, f"3/3 {s}"),
            )
            session.commit()
            finish_job(job_id, {
                "chapter_number": chapter_number,
                "status": "pending_review",
                "extraction_stats": stats,
                "summaries_rebuilt": rebuilt,
            })
        except Exception as exc:  # noqa: BLE001 — 失败整体回滚,保持 quarantined
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


# ---------- 正文版本历史:新旧对比与回滚 ----------


class ChapterVersionBrief(BaseModel):
    """版本列表项(不含全文,轻量)。"""

    id: int
    version: int
    source: str
    word_count: int
    created_at: str

    model_config = {"from_attributes": True}


class ChapterVersionDetail(ChapterVersionBrief):
    final_content: str
    draft_content: str


def _get_chapter_or_404(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚未生成")
    return ch


def _version_brief(v: ChapterVersion) -> ChapterVersionBrief:
    return ChapterVersionBrief(
        id=v.id, version=v.version, source=v.source,
        word_count=v.word_count, created_at=v.created_at.isoformat(),
    )


@router.get("/{chapter_number}/versions", response_model=list[ChapterVersionBrief])
async def list_versions(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """本章的历史正文版本(最新在前,不含全文)。每条是一次被覆盖前的快照。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    rows = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == ch.id)
        .order_by(ChapterVersion.version.desc())
        .all()
    )
    return [_version_brief(v) for v in rows]


@router.get(
    "/{chapter_number}/versions/{version_id}", response_model=ChapterVersionDetail
)
async def get_version(
    project_id: int, chapter_number: int, version_id: int,
    db: Session = Depends(get_db),
):
    """取某个历史版本的全文(用于新旧对比)。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    v = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.id == version_id, ChapterVersion.chapter_id == ch.id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return ChapterVersionDetail(
        id=v.id, version=v.version, source=v.source, word_count=v.word_count,
        created_at=v.created_at.isoformat(),
        final_content=v.final_content, draft_content=v.draft_content,
    )


@router.post("/{chapter_number}/versions/{version_id}/restore",
             response_model=ChapterDetail)
async def restore_version(
    project_id: int, chapter_number: int, version_id: int,
    db: Session = Depends(get_db),
):
    """回滚到某历史版本:先把当前正文留一版(source=restored),再换回该版正文。

    回滚只改正文;圣经/摘要仍停留在被弃版本,前端须随后调 re-extract-async 同步
    (与手动编辑保存一致)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    v = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.id == version_id, ChapterVersion.chapter_id == ch.id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if not v.final_content:
        raise HTTPException(status_code=400, detail="该版本无正文,无法回滚")
    snapshot_chapter(db, ch, source="restored")
    ch.final_content = v.final_content
    ch.draft_content = v.draft_content or ch.draft_content
    ch.word_count = len(ch.final_content)
    # 回滚后的正文未经审核,回到待审核(docs/08 §5.5)
    ch.status = "pending_review"
    db.commit()
    return ch


@router.get("", response_model=list[ChapterBrief])
async def list_chapters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return list(
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )


@router.get("/{chapter_number}", response_model=ChapterDetail)
async def get_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")
    resp = ChapterDetail.model_validate(ch, from_attributes=True)
    _fill_handoff(db, ch, resp)
    return resp
