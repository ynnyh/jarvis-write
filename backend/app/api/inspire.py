# app/api/inspire.py
# -*- coding: utf-8 -*-
"""灵感接口:碎片 → 多个故事方案;独立于项目,可在建项目前用。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.engines.consistency.extractor import parse_llm_json
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts.inspire import INSPIRE_PROMPT
from app.schemas.tendency import Tendency

router = APIRouter(
    prefix="/api/inspire",
    tags=["inspire"],
    dependencies=[Depends(get_current_user)],
)


class InspireRequest(BaseModel):
    spark: str = Field(default="", description="灵感碎片,可为空")
    tendency: Tendency = Field(default_factory=dict)
    count: int = Field(default=4, ge=2, le=6)


class Idea(BaseModel):
    title: str = ""
    logline: str = ""
    hook: str = ""
    twist: str = ""


class InspireResponse(BaseModel):
    ideas: list[Idea]


@router.post("", response_model=InspireResponse)
async def inspire(req: InspireRequest) -> InspireResponse:
    """从灵感碎片扩展故事方案(强模型,约1-2分钟)。"""
    assembled = assemble_tendency("outline", req.tendency)
    prompt = INSPIRE_PROMPT.format(
        spark=req.spark.strip() or "(空白,自由发挥)",
        count=req.count,
        style_directives=render_style_block(assembled),
    )
    try:
        raw = await get_adapter_for(Task.ARCHITECTURE).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"灵感生成失败: {exc}") from exc

    data = parse_llm_json(raw)
    ideas = [
        Idea(**{k: str(i.get(k, "")) for k in ("title", "logline", "hook", "twist")})
        for i in (data.get("ideas") or [])
        if isinstance(i, dict)
    ]
    if not ideas:
        raise HTTPException(status_code=502, detail="灵感解析失败,请重试")
    return InspireResponse(ideas=ideas)
