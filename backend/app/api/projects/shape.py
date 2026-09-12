# app/api/projects/shape.py
# -*- coding: utf-8 -*-
"""方案轮廓推荐:概念确认后,轻量模型按概念/题材推荐「阅读手感」与「篇幅轮廓」。

一次小调用(FAST 档,小输出):tone/elements 从倾向目录标签池里选(非法标签
丢弃),scale 三档选一;每项附一句话依据。定位是**预填默认**——用户在任何
对应步骤都能改,不锁定。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.engines.tendency import get_node_catalog
from app.llm.router import Task, get_adapter_for

from ._common import _get_project_or_404

router = APIRouter()

_OUTLINE_NODE = "outline"
_TONE_DIM = "tone"
_ELEMENTS_DIM = "elements"
_SCALES = ("short", "mid", "long", "serial")

_PROMPT = """\
你是资深网文责编。根据下面这本书的概念与题材,推荐「阅读手感」与「篇幅轮廓」。

【故事概念】
一句话主线:{logline}
设定:{setting}
【题材】{genre}

【可选基调标签(从中选 2-3 个)】{tone_labels}
【可选元素标签(从中选 0-3 个,可不选)】{elements_labels}
【篇幅档位(四选一)】
- short:短篇,约 20 章 × 3000 字(单一主线,一口气讲完)
- mid:中篇,约 60 章 × 3000 字(主线 + 一条副线,完整起承转合)
- long:长篇,约 150 章 × 3000 字(单卷完整大故事)
- serial:连载,百万字级(多卷滚动,体量由题材决定——群像/多线/大世界观才推荐)

判断依据:
- 篇幅看概念体量:单一起承转合 → short;主线 + 一条副线 → mid;多线群像/大世界观 → long
- 基调标签只从上面给的池子里选,不要发明新标签
- 每项给一句话依据

严格输出 JSON(不要 markdown 围栏,不要解释):
{{"tone": ["标签", "标签"], "elements": ["标签"], "scale": "short",
  "tone_reason": "一句话依据", "scale_reason": "一句话依据"}}
"""


class ShapeSuggestion(BaseModel):
    tone: list[str] = []
    elements: list[str] = []
    scale: str = "mid"
    tone_reason: str = ""
    scale_reason: str = ""


def _dim_labels(node_data: dict, dim_key: str) -> list[str]:
    for dim in node_data.get("dimensions", []):
        if dim.get("key") == dim_key:
            return [c.get("label", "") for c in dim.get("chips", []) if c.get("label")]
    return []


def _match_labels(raw_items: list, pool: list[str], cap: int) -> list[str]:
    """模型输出 → 目录标签:先精确匹配,再包含匹配(「悬疑烧脑」→「悬疑」)。

    模型总爱在池子标签上添字(实测「诙谐甜宠」「浪漫张力」),严格等值会把
    预选全过滤空,界面上「已预选」却一个芯片都没亮。按「池标签与模型串互为
    子串」回收;一个模型串可同时命中多个池标签(诙谐甜宠 → 诙谐 + 甜宠,
    分别落在基调池和元素池),仍只从池子里回收,不发明新标签。
    """
    picked: list[str] = []
    for item in raw_items:
        s = str(item).strip()
        if not s:
            continue
        hits = [s] if s in pool else [lab for lab in pool if lab in s or s in lab]
        for lab in sorted(hits, key=len, reverse=True):
            if lab not in picked:
                picked.append(lab)
    return picked[:cap]


@router.post("/{project_id}/suggest-shape", response_model=ShapeSuggestion)
async def suggest_shape(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """概念确认后推荐阅读手感与篇幅:一次轻量调用,结果只作预填默认,用户随时可改。"""
    project = _get_project_or_404(db, project_id)
    node = get_node_catalog(_OUTLINE_NODE)
    tone_labels = _dim_labels(node, _TONE_DIM)
    elements_labels = _dim_labels(node, _ELEMENTS_DIM)

    concept = project.concept or {}
    logline = str(concept.get("logline") or project.topic or "")[:300]
    setting = str(concept.get("setting") or "")[:200]

    prompt = _PROMPT.format(
        logline=logline or "(未填写)",
        setting=setting or "(未填写)",
        genre=project.genre or "不限",
        tone_labels="、".join(tone_labels),
        elements_labels="、".join(elements_labels),
    )
    adapter = get_adapter_for(Task.SUMMARY, max_tokens=400, timeout=60)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 失败原因直接反馈给前端
        raise HTTPException(status_code=502, detail=f"推荐生成失败: {exc}") from exc

    data = parse_llm_json(raw) or {}
    tone = _match_labels(data.get("tone") or [], tone_labels, 3)
    elements = _match_labels(data.get("elements") or [], elements_labels, 3)
    scale = data.get("scale") if data.get("scale") in _SCALES else "mid"
    return ShapeSuggestion(
        tone=tone,
        elements=elements,
        scale=scale,
        tone_reason=str(data.get("tone_reason") or "")[:120],
        scale_reason=str(data.get("scale_reason") or "")[:160],
    )
