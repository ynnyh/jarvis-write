# app/api/projects/style_profile.py
# -*- coding: utf-8 -*-
"""创作偏好档案(贯穿全书的创作宪法,注入所有生成环节)。

档案存在 project.global_tendency["_profile"] 子字典里,复用现成的倾向拼装器
(assemble_tendency/render_style_block)注入到生成/重写/定稿/润色/大纲/架构所有
prompt,零新增注入点。读改写都在服务端合并,避免前端整段覆盖 global_tendency
时把标签倾向冲掉。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Chapter, Project
from app.db.session import get_db
from app.llm.router import Task, get_adapter_for
from app.prompts.profile import PROFILE_ABSORB_PROMPT, PROFILE_EXTRACT_PROMPT

from ._common import _get_project_or_404

router = APIRouter()

logger = logging.getLogger("jarvis-write.api")
_PROFILE_FIELDS = ("style", "taboos", "audience", "other")


def _read_profile(project: Project) -> dict:
    profile = (project.global_tendency or {}).get("_profile") or {}
    return {k: str(profile.get(k) or "") for k in _PROFILE_FIELDS}


def _write_profile(project: Project, profile: dict) -> dict:
    """合并写回 global_tendency._profile(保留其余倾向标签),返回规范化后的档案。"""
    cleaned = {k: str(profile.get(k) or "").strip() for k in _PROFILE_FIELDS}
    tendency = dict(project.global_tendency or {})
    if any(cleaned.values()):
        tendency["_profile"] = cleaned
    else:
        tendency.pop("_profile", None)  # 全空则去掉键,注入时该块整体省略
    project.global_tendency = tendency
    return cleaned


def _parse_profile_json(raw: str) -> dict:
    """从模型输出里抠出档案 JSON(容忍代码块包裹与前后多余文字)。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("档案不是 JSON 对象")
    return {k: str(obj.get(k) or "") for k in _PROFILE_FIELDS}


class StyleProfileOut(BaseModel):
    style: str = ""
    taboos: str = ""
    audience: str = ""
    other: str = ""


@router.get("/{project_id}/style-profile", response_model=StyleProfileOut)
async def get_style_profile(project_id: int, db: Session = Depends(get_db)):
    """读取这本书的创作偏好档案(未设置时四字段皆空)。"""
    project = _get_project_or_404(db, project_id)
    return StyleProfileOut(**_read_profile(project))


class StyleProfileUpdate(BaseModel):
    style: str | None = None
    taboos: str | None = None
    audience: str | None = None
    other: str | None = None


@router.put("/{project_id}/style-profile", response_model=StyleProfileOut)
async def update_style_profile(
    project_id: int, req: StyleProfileUpdate, db: Session = Depends(get_db)
):
    """保存创作偏好档案:传了的字段(含空串)覆盖,未传的沿用现值。"""
    project = _get_project_or_404(db, project_id)
    current = _read_profile(project)
    for k, v in req.model_dump().items():
        if v is not None:
            current[k] = v
    cleaned = _write_profile(project, current)
    db.commit()
    return StyleProfileOut(**cleaned)


class StyleProfileAbsorbRequest(BaseModel):
    directive: str = Field(min_length=1, max_length=2000, description="对话蒸馏出的创作主张")


@router.post("/{project_id}/style-profile/absorb", response_model=StyleProfileOut)
async def absorb_style_profile(
    project_id: int, req: StyleProfileAbsorbRequest, db: Session = Depends(get_db)
):
    """把对话里聊出的创作主张,用 LLM 归类合并进档案对应字段后保存。

    吸收失败(模型/解析异常)时降级:把原文并进「其他创作主张」,不丢用户想法。
    """
    project = _get_project_or_404(db, project_id)
    current = _read_profile(project)
    directive = req.directive.strip()
    try:
        adapter = get_adapter_for(Task.SUMMARY)
        prompt = PROFILE_ABSORB_PROMPT.format(
            style=current["style"] or "(空)",
            taboos=current["taboos"] or "(空)",
            audience=current["audience"] or "(空)",
            other=current["other"] or "(空)",
            directive=directive,
        )
        merged = _parse_profile_json(await adapter.ask(prompt))
        cleaned = _write_profile(project, merged)
    except Exception:  # noqa: BLE001 — 降级:并进其他主张,不阻塞
        logger.warning("档案吸收失败,降级并入其他主张", exc_info=True)
        other = current["other"]
        current["other"] = f"{other};{directive}".strip("; ") if other else directive
        cleaned = _write_profile(project, current)
    db.commit()
    return StyleProfileOut(**cleaned)


# 提炼语料的截断预算:控制 token,正文按章截断、整块封顶
_EXTRACT_CHAPTER_CHARS = 2500
_EXTRACT_MAX_CHAPTERS = 4
_EXTRACT_CONTEXT_CAP = 14000


def _build_extract_context(db: Session, project: Project) -> str:
    """拼装这本书的现有内容,供反向提炼档案:概念 + 架构 + 简介 + 抽样正文。"""
    parts: list[str] = []
    if project.topic:
        parts.append(f"【一句话故事】\n{project.topic.strip()}")
    if project.synopsis:
        parts.append(f"【书籍简介】\n{project.synopsis.strip()[:1500]}")
    arch = project.architecture
    if arch is not None:
        arch_bits = [
            f"核心种子:{arch.core_seed}",
            f"角色动力学:{arch.character_dynamics}",
            f"世界观:{arch.world_building}",
            f"情节架构:{arch.plot_architecture}",
        ]
        parts.append("【顶层架构】\n" + "\n".join(b for b in arch_bits if b.split(":", 1)[-1].strip()))
    # 抽样已成文的章节(首/中/尾附近),归纳文风主要靠正文
    chapters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.status.in_(["pending_review", "approved", "stale"]),
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    if chapters:
        idxs = sorted({0, len(chapters) // 2, len(chapters) - 1})
        if len(chapters) >= _EXTRACT_MAX_CHAPTERS:
            step = len(chapters) / _EXTRACT_MAX_CHAPTERS
            idxs = sorted(set(int(i * step) for i in range(_EXTRACT_MAX_CHAPTERS)) | {len(chapters) - 1})
        samples = []
        for i in idxs[:_EXTRACT_MAX_CHAPTERS]:
            ch = chapters[i]
            text = (ch.final_content or "").strip()
            if text:
                samples.append(f"第{ch.chapter_number}章(节选):\n{text[:_EXTRACT_CHAPTER_CHARS]}")
        if samples:
            parts.append("【正文节选(归纳文风用)】\n" + "\n\n".join(samples))
    context = "\n\n".join(parts)
    return context[:_EXTRACT_CONTEXT_CAP]


@router.post("/{project_id}/style-profile/extract", response_model=StyleProfileOut)
async def extract_style_profile(project_id: int, db: Session = Depends(get_db)):
    """从这本书已有的内容反向提炼创作偏好档案,并直接落库启用(直接启用)。

    用于已生成的书:不用作者手填,就有一份与正文相符的档案。提炼失败不保存。
    """
    project = _get_project_or_404(db, project_id)
    context = _build_extract_context(db, project)
    if len(context.strip()) < 20:
        raise HTTPException(status_code=400, detail="这本书还没有内容,先写点再来提炼")
    try:
        adapter = get_adapter_for(Task.SUMMARY)
        extracted = _parse_profile_json(
            await adapter.ask(PROFILE_EXTRACT_PROMPT.format(context=context))
        )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("档案提炼失败", exc_info=True)
        raise HTTPException(status_code=502, detail="提炼失败,请稍后重试") from None
    cleaned = _write_profile(project, extracted)
    db.commit()
    return StyleProfileOut(**cleaned)
