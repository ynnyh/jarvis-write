# app/api/chapters/issues.py
# -*- coding: utf-8 -*-
"""一致性门禁:问题清单、状态流转、按问题修订。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, ChapterIssue, Project
from app.db.session import SessionLocal, get_db
from app.engines.consistency.checker import blockers_of, check_chapter, persist_issues
from app.engines.editorial import apply_gate_fixes, content_hash, repair_chapter
from app.engines.pipeline.chapter import _rolling_summary, generate_chapter
from app.engines.pipeline.handoff import handoff_payload
from app.jobs import create_job, fail_job, finish_job, fire_and_track, list_running, normalize_job_error, update_stage
from app.schemas.canon import coerce_canon

from ._common import _flavor_dict, _gate_payload, _get_chapter_or_404

router = APIRouter()


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
    # 仅 source=canon 的建议带结构化载荷 {kind: absence|device|deadline, ...},余者 None
    payload: dict | None = None


class IssuePatchRequest(BaseModel):
    """问题状态流转(docs/08 §5.5):open → resolved / ignored(单向)。"""

    status: str = Field(min_length=1)


def _issue_out(r: ChapterIssue) -> ChapterIssueOut:
    return ChapterIssueOut(
        id=r.id, source=r.source, severity=r.severity, issue_type=r.issue_type,
        description=r.description, evidence=r.evidence, suggestion=r.suggestion,
        status=r.status, created_at=r.created_at.isoformat(), payload=r.payload,
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
    return [_issue_out(r) for r in rows]


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
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


@router.post("/{chapter_number}/issues/{issue_id}/spot-repair")
async def spot_repair_issue(
    project_id: int, chapter_number: int, issue_id: int,
    db: Session = Depends(get_db),
):
    """单条问题定点修复(分级回炉的手动入口):AI 原位改句,不整章重写。

    与 apply-revision(整章重写)相对的轻量路径:repair_chapter 出「逐字锚 →
    最小改动」替换对(与生成回炉循环同一套实现),唯一锚校验应用后重跑门禁,
    **复查干净才落库**——复查仍有 blocker 则什么都不写,issue 保持 open,
    结果里建议改走按建议修订。quarantined 章修干净后仍处隔离:放行(补走
    抽取/摘要/契约)留给用户点 gate-release,不自动放行。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if issue.status != "open":
        raise HTTPException(status_code=400, detail=f"该问题已是 {issue.status} 状态,无需修复")
    if not issue.evidence.strip():
        raise HTTPException(
            status_code=400,
            detail="该问题没有逐字证据,无法定点定位;请走「按建议修订」或人工修改",
        )
    if not ch.final_content.strip():
        raise HTTPException(status_code=400, detail="本章尚无定稿正文,无法定点修复")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再修复。",
        )
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")
    # chapter_issues 不存 conflicting_fact(检查时的对照事实),修复 prompt 里缺省即可
    issue_payload = {
        "severity": issue.severity,
        "type": issue.issue_type,
        "description": issue.description,
        "evidence": issue.evidence,
        "suggestion": issue.suggestion,
    }

    async def runner() -> None:
        session = SessionLocal()
        try:
            session.get(Project, project_id)
            ch2 = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
                .first()
            )
            update_stage(job_id, "1/3 生成定点修复方案")
            old_text = ch2.final_content
            fixes = await repair_chapter(chapter_number, old_text, [issue_payload])
            new_text, applied, failed = apply_gate_fixes(old_text, fixes)
            if not applied:
                finish_job(job_id, {
                    "ok": False,
                    "reason": "AI 给出的修改在正文中定位不到(证据失配或片段不唯一),正文未改动",
                    "failed": failed,
                })
                return
            update_stage(job_id, "2/3 应用修改,重跑一致性门禁复查")
            # 复查对照源与生成时同口径(含滚动摘要);失败即一切未写,无需回滚动作
            rolling = _rolling_summary(session, project_id, chapter_number)
            recheck = await check_chapter(
                session, project_id, chapter_number, new_text, rolling_summary=rolling
            )
            reblockers = blockers_of(recheck)
            if reblockers:
                finish_job(job_id, {
                    "ok": False,
                    "reason": "定点修复后门禁复查仍有硬矛盾,本次修改未生效;建议改走「按建议修订」",
                    "recheck": reblockers[:3],
                })
                return
            update_stage(job_id, "3/3 落库")
            snapshot_chapter(session, ch2, source="spot_repair")
            ch2.final_content = new_text
            ch2.word_count = len(new_text)
            fixed_row = session.get(ChapterIssue, issue_id)
            fixed_row.status = "resolved"
            # 决议指纹锚定修复后的正文:persist 清账只删「指纹已变」的旧记录,
            # 不改指纹这条 resolved 会被当成过期记录清掉,面板上就看不到已解决了
            fixed_row.content_hash = content_hash(new_text)
            # 复查发现的 major/minor(含未修干净的同类问题)照常重建 open,面板可见不漏报
            persist_issues(session, ch2, recheck, source="gate", text=new_text)
            session.commit()
            finish_job(job_id, {
                "ok": True,
                "applied": applied,
                "failed": failed,
                "word_count": len(new_text),
                "status": ch2.status,
                "final_content": new_text,
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


def _adopt_into_canon(current: dict | None, payload: dict) -> tuple[dict, bool]:
    """把一条 canon 建议 payload 合并进现有 canon,返回(新 canon dict, 是否有变更)。

    幂等:已存在(同名装置 / 同文留白 / 已设倒计时)则不重复、不覆盖。合并后统一走
    coerce_canon 归一(整型/重要度/丢无名),与 PATCH 落库口径一致,单一真相源。
    """
    raw = coerce_canon(current).model_dump()  # 现有 canon 归一成 {absences,devices,deadline}
    kind = str(payload.get("kind") or "").strip().lower()
    changed = False

    if kind == "absence":
        txt = str(payload.get("text") or "").strip()
        if txt and not any(str(a).strip() == txt for a in raw["absences"]):
            raw["absences"].append(txt)
            changed = True
    elif kind == "device":
        name = str(payload.get("name") or "").strip()
        if name and not any(str(d.get("name") or "").strip() == name for d in raw["devices"]):
            raw["devices"].append({
                "name": name,
                "cadence": str(payload.get("cadence") or "").strip(),
                "importance": payload.get("importance") or "major",
            })
            changed = True
    elif kind == "deadline":
        name = str(payload.get("name") or "").strip()
        cur_dl = raw.get("deadline")
        if name and not (isinstance(cur_dl, dict) and str(cur_dl.get("name") or "").strip()):
            raw["deadline"] = {
                "name": name,
                "total_days": payload.get("total_days") or 0,
                "anchor_chapter": payload.get("anchor_chapter") or 1,
                "importance": "critical",
            }
            changed = True

    return coerce_canon(raw).model_dump(), changed


@router.post("/{chapter_number}/issues/{issue_id}/adopt-canon")
async def adopt_canon_suggestion(
    project_id: int, chapter_number: int, issue_id: int,
    db: Session = Depends(get_db),
):
    """采纳一条「故事宪法建议」(source=canon)进 project.canon,并标 issue 为 resolved。

    这是 LLM 提议→人工确认的落点:抽取器只把建议落成 advisory issue、绝不自动改 canon;
    真正写 canon 只在此处(作者触发的单独小事务,合规「canon 只在作者编辑时单写」)。
    幂等:建议内容若已在 canon 里(changed=False)也照常标 resolved(视作已采纳)。
    返回更新后的 canon 与 issue,供前端同时刷新宪法编辑器与问题清单。
    """
    project = get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if issue.source != "canon" or not isinstance(issue.payload, dict):
        raise HTTPException(status_code=400, detail="该问题不是可采纳的故事宪法建议")
    if issue.status != "open":
        raise HTTPException(
            status_code=400, detail=f"该建议已是 {issue.status} 状态,无需再采纳"
        )
    merged, changed = _adopt_into_canon(project.canon, issue.payload)
    project.canon = merged
    issue.status = "resolved"
    db.commit()
    db.refresh(project)
    db.refresh(issue)
    return {
        "ok": True,
        "changed": changed,  # False = 该建议内容此前已在宪法里(仍标记为已采纳)
        "canon": project.canon,
        "issue": _issue_out(issue),
    }
