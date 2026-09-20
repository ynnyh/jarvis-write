# app/api/projects/skeleton.py
# -*- coding: utf-8 -*-
"""故事骨架(docs/20 两段式点火):分段的走向墙 + 逐段铺章。

存的是 project.macro_plan(卷纲与骨架同字段,不建第二份状态)。分段形状升级为
{start, end, title, goal, conflict, start_state, end_state, confirmed, locked};
存量卷纲缺新增字段时按读时补默认(已铺蓝图的老段视作 confirmed,不惊动存量书)。

与「一枪出全书蓝图」(blueprint-async,信任模式)并存:向导默认走骨架墙
(逐段确认→逐段铺章),信任模式按钮直通旧链路——确认是权利不是门槛。
锁定分段铁律(docs/20 铁律 2):重出骨架保留 locked 段;铺章遇到 outline.locked 章
由 save_blueprint 短路(双保险)。
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Outline, Project
from app.db.session import SessionLocal, get_db
from app.engines.consistency.extractor import parse_llm_json
from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import dna_block_of, render_style_block
from app.jobs import (
    create_job, fail_job, finish_job, fire_and_track, list_running,
    normalize_job_error, update_stage,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.rolling import SKELETON_PROMPT
from app.schemas.project import GenerateBlueprintRequest, OutlineOut

from ._common import _get_project_or_404
from .blueprint import _arch_text, _core_premise_text, _sequel_prev_block

router = APIRouter()

# 骨架段大小:向导墙上一屏看得下的粒度(计划 §4.2:10-20 章/段)
_SKELETON_SIZE_SMALL = 10   # 目标 ≤ 40 章:10 章/段
_SKELETON_SIZE = 20


def _segment_size(target: int) -> int:
    return _SKELETON_SIZE_SMALL if target <= 40 else _SKELETON_SIZE


def _upgrade_segments(raw: list | None, project_id: int, db: Session) -> list[dict]:
    """读时补默认:存量卷纲(无新字段)升级为骨架形状。

    已铺蓝图的老段视作 confirmed(它们在真实使用中,不能因为升级字段被回退);
    没铺蓝图的老段 confirmed=False,进墙走确认。
    """
    if not isinstance(raw, list):
        return []
    paved: set[int] = {
        n for (n,) in db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id).all()
    }
    out: list[dict] = []
    for seg in raw:
        if not isinstance(seg, dict) or not str(seg.get("goal") or "").strip():
            continue
        rng = range(int(seg.get("start") or 0), int(seg.get("end") or 0) + 1)
        out.append({
            "start": int(seg.get("start") or 0),
            "end": int(seg.get("end") or 0),
            "title": str(seg.get("title") or ""),
            "goal": str(seg.get("goal") or ""),
            "conflict": str(seg.get("conflict") or ""),
            "start_state": str(seg.get("start_state") or ""),
            "end_state": str(seg.get("end_state") or ""),
            "confirmed": bool(seg.get("confirmed", any(n in paved for n in rng))),
            "locked": bool(seg.get("locked", False)),
        })
    return out


class SegmentEdit(BaseModel):
    title: str | None = None
    goal: str | None = None
    conflict: str | None = None
    start_state: str | None = None
    end_state: str | None = None


@router.get("/{project_id}/skeleton")
def get_skeleton(project_id: int, db: Session = Depends(get_db)):
    """当前骨架(读时补默认)。空表 = 还没铺骨架。"""
    project = _get_project_or_404(db, project_id)
    return {
        "segments": _upgrade_segments(project.macro_plan, project_id, db),
        "target_chapters": project.target_chapters,
        "segment_size": _segment_size(project.target_chapters),
    }


@router.post("/{project_id}/skeleton-async")
async def generate_skeleton_async(
    project_id: int, req: GenerateBlueprintRequest, db: Session = Depends(get_db)
):
    """生成/重出故事骨架(job: skeleton-{pid})。

    重出保留 locked 段(铁律 2);confirmed 段的目标作为「已定走向」注入,
    让新骨架与作者已拍板的段落相容,而不是推倒重来。
    """
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=400, detail="请先生成顶层架构")
    for jid, _job in list_running(f"skeleton-{project_id}"):
        if _job["kind"] == f"skeleton-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"skeleton-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            style_block = render_style_block(
                assemble_tendency("outline", req.tendency, p.global_tendency)
            ) + dna_block_of(p.dna)
            old = _upgrade_segments(p.macro_plan, p.id, session)
            kept_locked = [s for s in old if s["locked"]]
            confirmed_goals = [
                f"第{s['start']}-{s['end']}章《{s['title'] or '未命名'}》:{s['goal']}"
                for s in old if s["confirmed"] and not s["locked"]
            ]
            # 未锁定段之外的全部区间重出:locked 段原样保留,其余按当前体量切
            size = _segment_size(p.target_chapters)
            count = math.ceil(p.target_chapters / size)
            hard_note = f"\n注意:全书共 {p.target_chapters} 章是作者拍板的体量承诺,分段与节奏必须按这个总章数规划,不得擅自压缩或膨胀总章数。"
            if confirmed_goals:
                hard_note += (
                    "\n注意:以下段落作者已拍板,新骨架的其余段落必须与它们相容"
                    "(衔接处状态连续,不得矛盾):\n" + "\n".join(confirmed_goals)
                )
            update_stage(job_id, "生成故事骨架")
            raw = await get_adapter_for(Task.BLUEPRINT).ask(
                SKELETON_PROMPT.format(
                    number_of_chapters=p.target_chapters,
                    novel_architecture=_arch_text(p),
                    style_directives=style_block,
                    segment_count=count,
                    segment_size=size,
                    hard_note=hard_note,
                )
            )
            data = parse_llm_json(raw)
            gen = [
                s for s in (data.get("segments") or [])
                if isinstance(s, dict) and str(s.get("goal") or "").strip()
            ]
            # 模型少铺了段:兜底补一段到结尾(与卷纲同款纪律)
            if len(gen) < count:
                gen.append({
                    "title": "终局", "goal": "收束全部主线与伏笔,完成架构中的终局。",
                    "conflict": "最终决战", "start_state": "全线压顶", "end_state": "尘埃落定",
                })
            segments: list[dict] = list(kept_locked)
            cursor = 1
            for s in gen:
                while any(k["start"] <= cursor <= k["end"] for k in kept_locked):
                    cursor = kept_locked and max(k["end"] for k in kept_locked) + 1 or cursor
                end = min(cursor + size - 1, p.target_chapters)
                if end < cursor:
                    break
                segments.append({
                    "start": cursor, "end": end,
                    "title": str(s.get("title") or ""),
                    "goal": str(s["goal"]).strip(),
                    "conflict": str(s.get("conflict") or ""),
                    "start_state": str(s.get("start_state") or ""),
                    "end_state": str(s.get("end_state") or ""),
                    "confirmed": False, "locked": False,
                })
                cursor = end + 1
            if not segments:
                raise RuntimeError("骨架生成失败(模型输出无法解析),请重试。")
            p.macro_plan = segments
            session.commit()
            finish_job(job_id, {"segments": segments})
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


def _mutate_segments(project: Project, fn) -> list[dict]:
    """拷贝-改-赋回地改 macro_plan,返回新列表。

    JSON 列的原地改不触发变更追踪:segs = project.macro_plan 拿到的是同一个
    对象,改完再赋回去,flush 时新旧相等被视为未变更,commit 空转——拍板/锁定
    曾因此「响应成功、库没写」的静默失败(2026-09-20 实测踩中)。必须赋一个新对象。
    """
    segs = [dict(s) for s in (project.macro_plan or [])]
    fn(segs)
    project.macro_plan = segs
    return segs


@router.put("/{project_id}/skeleton/{index}")
def edit_segment(
    project_id: int, index: int, body: SegmentEdit, db: Session = Depends(get_db)
):
    """编辑分段的人话字段(段名/目标/冲突/起止状态)。章号区间不在此改(重出骨架改)。"""
    project = _get_project_or_404(db, project_id)
    if index < 0 or index >= len(project.macro_plan or []):
        raise HTTPException(status_code=404, detail="分段不存在")

    def _edit(segs: list[dict]) -> None:
        seg = segs[index]
        for field in ("title", "goal", "conflict", "start_state", "end_state"):
            v = getattr(body, field)
            if v is not None:
                seg[field] = v.strip()

    segs = _mutate_segments(project, _edit)
    db.commit()
    return {"segment": segs[index]}


@router.post("/{project_id}/skeleton/{index}/confirm")
def confirm_segment(
    project_id: int, index: int, confirmed: bool, db: Session = Depends(get_db)
):
    """拍板/撤回一个分段:confirmed 才能铺章。"""
    project = _get_project_or_404(db, project_id)
    if index < 0 or index >= len(project.macro_plan or []):
        raise HTTPException(status_code=404, detail="分段不存在")

    def _confirm(segs: list[dict]) -> None:
        segs[index]["confirmed"] = bool(confirmed)

    segs = _mutate_segments(project, _confirm)
    db.commit()
    return {"segment": segs[index]}


@router.post("/{project_id}/skeleton/{index}/lock")
def lock_segment(
    project_id: int, index: int, locked: bool, db: Session = Depends(get_db)
):
    """锁定/解锁分段:locked 段重出骨架时原样保留(铁律 2)。"""
    project = _get_project_or_404(db, project_id)
    if index < 0 or index >= len(project.macro_plan or []):
        raise HTTPException(status_code=404, detail="分段不存在")

    def _lock(segs: list[dict]) -> None:
        segs[index]["locked"] = bool(locked)
        if locked:
            segs[index]["confirmed"] = True  # 锁定即拍板

    segs = _mutate_segments(project, _lock)
    db.commit()
    return {"segment": segs[index]}


@router.post("/{project_id}/pave-async")
async def pave_segment_async(
    project_id: int, req: GenerateBlueprintRequest, segment: int,
    db: Session = Depends(get_db),
):
    """按已确认分段铺章节蓝图(job: pave-{pid}-{idx})。

    只铺该段区间;段内 outline.locked 的章由 save_blueprint 短路(重铺保护)。
    """
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=400, detail="请先生成顶层架构")
    segs = _upgrade_segments(project.macro_plan, project_id, db)
    if segment < 0 or segment >= len(segs):
        raise HTTPException(status_code=404, detail="分段不存在")
    seg = segs[segment]
    if not seg["confirmed"]:
        raise HTTPException(status_code=400, detail="这一段还没确认,先拍板再铺章")
    kind = f"pave-{project_id}-{segment}"
    for jid, _job in list_running(f"pave-{project_id}-"):
        if _job["kind"] == kind:
            return {"job_id": jid}
    job_id = create_job(kind)

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            segs_local = _upgrade_segments(p.macro_plan, p.id, session)
            seg_local = segs_local[segment]
            # 段落上下文:本段目标/冲突/起止状态 + 上一段尾部衔接
            prev = segs_local[segment - 1] if segment > 0 else None
            ctx_lines = [
                f"【本段走向(作者已拍板,蓝图必须严格执行)】",
                f"段名:{seg_local['title'] or '未命名'}(第{seg_local['start']}-{seg_local['end']}章)",
                f"段目标:{seg_local['goal']}",
            ]
            if seg_local["conflict"]:
                ctx_lines.append(f"本段主线冲突:{seg_local['conflict']}")
            if seg_local["start_state"]:
                ctx_lines.append(f"进段时主角状态:{seg_local['start_state']}")
            if seg_local["end_state"]:
                ctx_lines.append(f"出段时主角状态:{seg_local['end_state']}")
            if prev:
                tail = (
                    session.query(Outline)
                    .filter(Outline.project_id == p.id, Outline.chapter_number == prev["end"])
                    .first()
                )
                if tail is not None:
                    ctx_lines.append(f"上一段收束于第{prev['end']}章《{tail.title}》:{tail.summary}")
            chapters, warnings = await generate_blueprint(
                core_premise=_core_premise_text(session, p.id),
                novel_architecture=_arch_text(p) + _sequel_prev_block(session, p.id) + "\n" + "\n".join(ctx_lines),
                number_of_chapters=p.target_chapters,
                tendency=req.tendency,
                global_tendency=p.global_tendency,
                progress=lambda s: update_stage(job_id, s),
                start_chapter=seg_local["start"],
                end_chapter=seg_local["end"],
                word_number=p.target_words_per_chapter,
            )
            update_stage(job_id, "落库中")
            outlines = save_blueprint(session, p, chapters)
            session.commit()
            finish_job(job_id, {
                "outlines": [OutlineOut.model_validate(o).model_dump() for o in outlines],
                "warnings": warnings,
                "planned_range": [seg_local["start"], seg_local["end"]],
                "segment": segment,
            })
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}
