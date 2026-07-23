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
from app.db.models import Chapter, ChapterVersion, Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.chapter import generate_chapter
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


class GenerateChapterResponse(ChapterDetail):
    """生成结果:正文 + 一致性检查问题 + 圣经抽取统计 + AI 味指数 + 字数守卫结果 + 审校把关结果。"""

    consistency_issues: list[dict] = []
    extraction_stats: dict = {}
    # AI 味指数:纯规则统计(不调 LLM,零额外耗时),生成完成即给出
    ai_flavor: dict = {}
    # 字数守卫:none / compressed / split
    word_guard_action: str = "none"
    split_info: dict = {}
    # 编辑部审校把关:scores/comment/suggestions/passed/revision_rounds/threshold
    review: dict = {}


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
        chapter, issues, stats, guard_result, review_result = await generate_chapter(
            db, project, chapter_number, req.tendency,
            revision=req.revision.strip(),
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
            chapter, issues, stats, guard_result, review_result = await generate_chapter(
                session, project, chapter_number, req.tendency,
                progress=lambda s: update_stage(job_id, s),
                revision=req.revision.strip(),
            )
            session.commit()
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
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
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
                chapter, _issues, _stats, _guard, _review = await generate_chapter(
                    session, project, n, req.tendency,
                    progress=lambda s, _n=n, _i=i: update_stage(
                        job_id, f"[{_i}/{total}] 第 {_n} 章:{s}"
                    ),
                )
                session.commit()
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
    ch.status = "finalized"
    db.commit()
    return ch


@router.post("/{chapter_number}/re-extract-async")
async def re_extract_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """手改正文后:重抽取(幂等,先清旧账)→ 重建下游摘要 → 更新向量库。"""
    get_project_or_404(db, project_id)
    # 同章同步任务已在跑 → 复用,不重复起
    for jid, job in list_running(f"re-extract-{project_id}-"):
        if job["kind"] == f"re-extract-{project_id}-{chapter_number}":
            return {"job_id": jid}
    job_id = create_job(f"re-extract-{project_id}-{chapter_number}")

    async def runner() -> None:
        from app.engines.consistency.extractor import extract_and_apply
        from app.engines.memory import ChapterMemory
        from app.engines.pipeline.chapter import rebuild_summaries_after

        # 同步要跨多轮 LLM 调用,期间用量记账等在别的连接提交,会让本连接的读快照过期,
        # 升级写锁时撞 SQLITE_BUSY(WAL 下不走 busy_timeout)。三步都幂等(抽取先清旧账 /
        # 摘要覆盖写 / 向量库删后插),故除尽量缩短事务外,再遇锁整体回滚重试兜底。
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
                update_stage(job_id, "1/3 重新抽取状态(清旧账)")
                stats = await extract_and_apply(
                    session, project_id, chapter_number, content
                )
                # 抽取写入立刻提交:别拿着写锁跨下游摘要的多轮 LLM 调用
                session.commit()
                update_stage(job_id, "2/3 重建下游前情摘要")
                rebuilt = await rebuild_summaries_after(
                    session, project, chapter_number,
                    progress=lambda s: update_stage(job_id, f"2/3 {s}"),
                )
                update_stage(job_id, "3/3 更新向量库")
                await ChapterMemory(project_id).add_chapter(chapter_number, content)
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
    ch.status = "finalized"
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
    return ch
