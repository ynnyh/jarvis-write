# app/api/projects/blueprint.py
# -*- coding: utf-8 -*-
"""章节蓝图:分块生成全书目录(同步/异步)+ 滚动规划(卷纲 / 展开下一卷)+ 目录查询。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.jobs import create_job, fail_job, finish_job, list_running, normalize_job_error, update_stage
from app.llm.router import Task, get_adapter_for
from app.schemas.project import (
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    OutlineOut,
)

from ._common import _get_project_or_404

router = APIRouter()


@router.post("/{project_id}/blueprint", response_model=GenerateBlueprintResponse)
async def generate_project_blueprint(
    project_id: int,
    req: GenerateBlueprintRequest,
    db: Session = Depends(get_db),
):
    """基于已有架构,分块生成全书章节蓝图并落库。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(
            status_code=400, detail="请先生成顶层架构(POST .../architecture)"
        )

    from app.engines.pipeline.architecture import ArchitectureResult

    arch_text = ArchitectureResult(
        core_seed=project.architecture.core_seed,
        character_dynamics=project.architecture.character_dynamics,
        world_building=project.architecture.world_building,
        plot_architecture=project.architecture.plot_architecture,
    ).full_text
    from app.engines.common import world_rules_block

    arch_text += world_rules_block(project)

    chapters, warnings = await generate_blueprint(
        novel_architecture=arch_text,
        number_of_chapters=project.target_chapters,
        tendency=req.tendency,
        global_tendency=project.global_tendency,
    )
    outlines = save_blueprint(db, project, chapters)
    db.commit()
    return GenerateBlueprintResponse(
        outlines=[OutlineOut.model_validate(o) for o in outlines],
        warnings=warnings,
    )


@router.post("/{project_id}/blueprint-async")
async def generate_project_blueprint_async(
    project_id: int,
    req: GenerateBlueprintRequest,
    db: Session = Depends(get_db),
):
    """异步生成蓝图:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看分块进度。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(
            status_code=400, detail="请先生成顶层架构(POST .../architecture)"
        )
    # 防重复提交:同项目蓝图任务已在跑 → 复用
    for jid, _job in list_running(f"blueprint-{project_id}"):
        if _job["kind"] == f"blueprint-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"blueprint-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            arch_text = _arch_text(p)
            # 滚动规划:长书只铺第一卷,先出卷纲定全书方向;短书一次铺完
            end_chapter = None
            if p.target_chapters > ROLLING_THRESHOLD:
                style_block = render_style_block(
                    assemble_tendency("outline", req.tendency, p.global_tendency)
                )
                update_stage(job_id, "生成全书卷纲(指南针)")
                segments = await _ensure_macro_plan(session, p, style_block)
                end_chapter = min(segments[0]["end"], p.target_chapters)

            chapters, warnings = await generate_blueprint(
                novel_architecture=arch_text,
                number_of_chapters=p.target_chapters,
                tendency=req.tendency,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
                end_chapter=end_chapter,
            )
            update_stage(job_id, "落库中")
            outlines = save_blueprint(session, p, chapters)
            session.commit()
            finish_job(job_id, {
                "outlines": [
                    OutlineOut.model_validate(o).model_dump() for o in outlines
                ],
                "warnings": warnings,
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


# ---------- 滚动规划:卷纲 + 分段蓝图 ----------

# 每卷章数与启用阈值:目标超过阈值的书走滚动规划(首铺一卷,写到卷尾再展开)
SEGMENT_SIZE = 30
ROLLING_THRESHOLD = 40


def _arch_text(p: Project) -> str:
    from app.engines.pipeline.architecture import ArchitectureResult

    from app.engines.common import world_rules_block

    return ArchitectureResult(
        core_seed=p.architecture.core_seed,
        character_dynamics=p.architecture.character_dynamics,
        world_building=p.architecture.world_building,
        plot_architecture=p.architecture.plot_architecture,
    ).full_text + world_rules_block(p)


async def _ensure_macro_plan(session, p: Project, style_block: str) -> list[dict]:
    """卷纲缺失时生成一次(指南针,全书方向锚点)。幂等。"""
    import math

    from app.engines.consistency.extractor import parse_llm_json
    from app.prompts.rolling import MACRO_PLAN_PROMPT

    if p.macro_plan:
        return p.macro_plan
    segment_count = math.ceil(p.target_chapters / SEGMENT_SIZE)
    prompt = MACRO_PLAN_PROMPT.format(
        number_of_chapters=p.target_chapters,
        novel_architecture=_arch_text(p),
        style_directives=style_block,
        segment_count=segment_count,
        segment_size=SEGMENT_SIZE,
    )
    raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
    data = parse_llm_json(raw)
    segments = []
    cursor = 1
    for seg in (data.get("segments") or [])[:segment_count]:
        if not isinstance(seg, dict) or not str(seg.get("goal") or "").strip():
            continue
        end = min(int(seg.get("end") or (cursor + SEGMENT_SIZE - 1)), p.target_chapters)
        if end < cursor:
            continue
        segments.append({"start": cursor, "end": end, "goal": str(seg["goal"]).strip()})
        cursor = end + 1
    # 模型没铺满目标章数:兜底补一卷到结尾
    if cursor <= p.target_chapters:
        segments.append({
            "start": cursor, "end": p.target_chapters,
            "goal": "收束全部主线与伏笔,完成架构中的终局。",
        })
    if not segments:
        raise RuntimeError("卷纲生成失败(模型输出无法解析),请重试。")
    p.macro_plan = segments
    session.commit()
    return segments


def _segment_for(segments: list[dict], chapter: int) -> tuple[dict, dict | None]:
    """chapter 所在卷及下一卷(无则 None)。"""
    for i, seg in enumerate(segments):
        if seg["start"] <= chapter <= seg["end"]:
            return seg, segments[i + 1] if i + 1 < len(segments) else None
    return segments[-1], None


def _written_state_block(session, p: Project, seg: dict, next_seg: dict | None) -> str:
    """展开下一卷时注入的已成文状态(前情摘要 + 未回收伏笔 + 卷目标)。"""
    from app.db.models import Chapter, Foreshadowing
    from app.db.models.summary import ChapterSummary
    from app.prompts.rolling import ROLLING_CONTEXT_BLOCK

    last_written = (
        session.query(Chapter)
        .filter(Chapter.project_id == p.id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number.desc())
        .first()
    )
    written_upto = last_written.chapter_number if last_written else 0
    srow = (
        session.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == p.id,
            ChapterSummary.chapter_number == written_upto,
        )
        .first()
        if written_upto
        else None
    )
    rolling = (srow.rolling_summary if srow else "") or "(尚无成文,按蓝图衔接上下卷)"
    fores = (
        session.query(Foreshadowing)
        .filter(
            Foreshadowing.project_id == p.id,
            Foreshadowing.status.in_(("planted", "reinforced")),
        )
        .order_by(Foreshadowing.chapter_planted)
        .limit(20)
        .all()
    )
    fore_lines = "\n".join(
        f"- 「{f.description}」(第{f.chapter_planted}章埋,预期第{f.expected_payoff_chapter or '?'}章收)"
        for f in fores
    ) or "(无)"
    return ROLLING_CONTEXT_BLOCK.format(
        start=seg["start"], end=seg["end"], segment_goal=seg["goal"],
        next_goal=(next_seg["goal"] if next_seg else "(已是最终卷,收束全书)"),
        written_upto=written_upto, rolling_summary=rolling[:2500],
        open_foreshadows=fore_lines,
    )


@router.post("/{project_id}/blueprint-extend-async")
async def extend_blueprint_async(project_id: int, db: Session = Depends(get_db)):
    """展开下一卷蓝图:按卷纲 + 已成文状态规划下一段章节。滚动规划核心。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=400, detail="请先生成顶层架构")
    max_outline = (
        db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number.desc())
        .first()
    )
    planned_upto = max_outline[0] if max_outline else 0
    if planned_upto == 0:
        raise HTTPException(status_code=400, detail="还没有首卷蓝图,请先「生成蓝图」")
    if planned_upto >= project.target_chapters:
        raise HTTPException(status_code=400, detail="全书蓝图已铺满,无需展开")
    for jid, _job in list_running(f"blueprint-{project_id}"):
        if _job["kind"] == f"blueprint-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"blueprint-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            style_block = render_style_block(
                assemble_tendency("outline", {}, p.global_tendency)
            )
            update_stage(job_id, "读取卷纲与前情状态")
            segments = await _ensure_macro_plan(session, p, style_block)
            start = planned_upto + 1
            seg, next_seg = _segment_for(segments, start)
            end = min(seg["end"], p.target_chapters)
            context = _written_state_block(session, p, seg, next_seg)
            # 上一卷蓝图尾部:衔接用
            tail_outlines = (
                session.query(Outline)
                .filter(Outline.project_id == project_id)
                .order_by(Outline.chapter_number.desc())
                .limit(4)
                .all()
            )
            prev_tail = "\n".join(
                f"第{o.chapter_number}章 {o.title}:{o.summary}"
                for o in reversed(tail_outlines)
            )
            chapters, warnings = await generate_blueprint(
                novel_architecture=_arch_text(p) + context,
                number_of_chapters=p.target_chapters,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
                start_chapter=start,
                end_chapter=end,
                previous_tail=prev_tail,
            )
            update_stage(job_id, "落库中")
            outlines = save_blueprint(session, p, chapters)
            session.commit()
            finish_job(job_id, {
                "outlines": [OutlineOut.model_validate(o).model_dump() for o in outlines],
                "warnings": warnings,
                "planned_range": [start, end],
            })
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("/{project_id}/outlines", response_model=list[OutlineOut])
async def list_outlines(
    project_id: int, db: Session = Depends(get_db)
) -> list[Outline]:
    _get_project_or_404(db, project_id)
    return list(
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    )
