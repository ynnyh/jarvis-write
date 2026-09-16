# app/api/projects/style_profile_api.py
# -*- coding: utf-8 -*-
"""文风画像 API(docs/20 同批「文风可视化+进化」)。

与既有「创作偏好档案」(/style-profile,style/taboos/audience/other+文风范本,
存 global_tendency._profile)互补:那边是创作主张,这边是六维笔法指令。

- GET  /{pid}/style-dimensions           画像 + 历史(画像卡投影)
- PUT  /{pid}/style-dimensions           作者保存(手改维度;当前版进历史,版本+1)
- POST /{pid}/style-dimensions/history/{version}/restore  回退到某版(回退也算一次保存)
- POST /{pid}/style-dimensions/reanalyze-async  重新分析:续集对前作重跑;普通书从
  自己已定稿章节提取(同一采样管线,常数开销)

一份真相:所有写路径都落 projects.style_profile,画像卡只是投影。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Chapter, Project, User
from app.db.session import get_db
from app.engines.style_profile import (
    PROFILE_DIMS,
    archive_history,
    merge_dims,
    normalize_profile,
    profile_from_analysis,
)
from app.jobs import spawn_job

from ._common import _get_project_or_404

router = APIRouter()

# 重分析的采样规模:从已定稿章节均匀取 6 段(与前作分析同一管线、同一常数开销)
_REANALYZE_SLICES = 6
_REANALYZE_CHARS = 2500


class ProfileSaveRequest(BaseModel):
    dims: dict = Field(default_factory=dict, description="{维度key: 文本};空维度不清已有内容")


def _profile_out(project: Project) -> dict:
    p = normalize_profile(project.style_profile)
    return {
        "dims": p["dims"],
        "version": p["version"],
        "history": p["history"],
        "dim_defs": [{"key": k, "label": label, "hint": hint} for k, label, hint in PROFILE_DIMS],
        "memo": project.style_memo or "",
    }


@router.get("/{project_id}/style-dimensions")
def get_style_profile(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = _get_project_or_404(db, project_id)
    return _profile_out(project)


@router.put("/{project_id}/style-dimensions")
def save_style_profile(
    project_id: int,
    req: ProfileSaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """作者保存画像:当前版进历史,新内容覆盖对应维度(版本+1)。"""
    project = _get_project_or_404(db, project_id)
    merged = merge_dims(project.style_profile, req.dims, source="手改")
    merged["history"] = archive_history(project.style_profile)
    merged["version"] = normalize_profile(project.style_profile)["version"] + 1
    project.style_profile = merged  # 重新赋值触发 JSON 变更追踪
    db.commit()
    return _profile_out(project)


@router.post("/{project_id}/style-dimensions/history/{version}/restore")
def restore_style_profile(
    project_id: int,
    version: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回退到历史版本(回退本身也是一次保存:当前版进历史,版本继续+1)。"""
    project = _get_project_or_404(db, project_id)
    p = normalize_profile(project.style_profile)
    target = next((h for h in p["history"] if h.get("version") == version), None)
    if target is None:
        raise HTTPException(status_code=404, detail="没有这个历史版本")
    current_dims = p["dims"]
    project.style_profile = {
        "dims": target.get("dims") or {},
        "version": p["version"] + 1,
        "history": archive_history({"dims": current_dims, "version": p["version"], "history": p["history"]}),
    }
    db.commit()
    return _profile_out(project)


@router.post("/{project_id}/style-dimensions/reanalyze-async")
async def reanalyze_style_profile_async(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重新分析文风画像:续集对前作重跑;普通书从自己已定稿章节提取。

    同一采样管线(均匀分层、常数开销),产物直接覆盖画像六维(旧版进历史)。
    """
    project = _get_project_or_404(db, project_id)
    source_pid = project.sequel_of_id or project_id

    async def work(progress):
        progress("采样正文(全书均匀分层)")
        from app.db.session import SessionLocal
        from app.engines.consistency.extractor import parse_llm_json
        from app.llm.router import Task, get_adapter_for

        session = SessionLocal()
        try:
            src = session.get(Project, source_pid)
            if src is None:
                raise RuntimeError("来源作品不存在")
            chapters = (
                session.query(Chapter)
                .filter(Chapter.project_id == src.id, Chapter.final_content != "")
                .order_by(Chapter.chapter_number)
                .all()
            )
            if not chapters:
                return {"dims": {}, "note": "来源作品还没有正文,无法分析"}
            n = len(chapters)
            idxs = sorted({round(i * (n - 1) / (_REANALYZE_SLICES - 1)) for i in range(_REANALYZE_SLICES)}) \
                if n > _REANALYZE_SLICES else list(range(n))
            samples = "\n\n".join(
                f"【第{chapters[i].chapter_number}章(节选)】"
                f"{chapters[i].final_content[:_REANALYZE_CHARS]}"
                for i in idxs
            )
            dim_lines = "\n".join(f'"{k}": "…({label}:{hint})…"' for k, label, hint in PROFILE_DIMS)
            progress("AI 提炼文风技法画像")
            raw = await get_adapter_for(Task.BLUEPRINT).ask(
                "\n".join([
                    "你是编辑部的主编。请从这部小说的正文采样里,提炼作者的**文风技法画像**——",
                    "写成给续集写手的可执行工作指令,不是形容词夸奖。",
                    "六个维度,每维 40-80 字,只输出 JSON:",
                    "{",
                    dim_lines,
                    "}",
                    "",
                    "【正文采样(全书均匀分层节选)】",
                    samples,
                ])
            )
            data = parse_llm_json(raw)
            new_profile = profile_from_analysis({"style_profile": data})
            target = session.get(Project, project_id)
            if target is None:
                raise RuntimeError("目标项目不存在")
            from app.engines.style_profile import normalize_profile as _norm

            old = _norm(target.style_profile)
            new_profile["version"] = old["version"] + 1
            from datetime import datetime, timezone

            new_profile["history"] = (
                old["history"]
                + [{"version": old["version"], "dims": old["dims"],
                    "at": datetime.now(timezone.utc).isoformat()}]
            )[-10:]
            target.style_profile = new_profile
            session.commit()
            return {"dims": new_profile["dims"], "note": "画像已更新"}
        finally:
            session.close()

    return {"job_id": spawn_job(f"style-analyze-{project_id}", work)}
