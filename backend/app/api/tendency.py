# app/api/tendency.py
# -*- coding: utf-8 -*-
"""倾向标签目录接口:前端渲染 chips 用。

GET  /api/tendency/catalog          全部生成节点的可选倾向
GET  /api/tendency/catalog/{node}   单节点目录
POST /api/tendency/genre-infer      按故事概念/文本推断题材大类 + 推荐流派(起步流预填用)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.engines.consistency.extractor import parse_llm_json
from app.engines.tendency import get_catalog, get_node_catalog
from app.llm.router import Task, get_adapter_for
from app.schemas.tendency import NodeCatalogOut

router = APIRouter(prefix="/api/tendency", tags=["tendency"])


@router.get("/catalog")
async def full_catalog() -> dict:
    """全部生成节点的可选倾向(outline/chapter/polish)。"""
    return get_catalog()


@router.get("/catalog/{node}", response_model=NodeCatalogOut)
async def node_catalog(node: str) -> NodeCatalogOut:
    """单个生成节点的可选倾向。"""
    try:
        data = get_node_catalog(node)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NodeCatalogOut(
        node=node, label=data.get("label", node), dimensions=data.get("dimensions", [])
    )


# ---------- 题材推断(起步流预填) ----------

def _genre_dimension() -> dict:
    for dim in get_node_catalog("outline").get("dimensions", []):
        if dim["key"] == "genre":
            return dim
    raise HTTPException(status_code=500, detail="题材维度缺失")


class GenreInferRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="概念/主题/任意描述")


class GenreInferResponse(BaseModel):
    category: str = Field(description="推断的大类 key")
    category_label: str = ""
    genre: str = Field(default="", description="最贴的流派 chip label(可能为空)")
    suggestions: list[dict] = Field(
        default_factory=list, description="推荐流派 chips(同大类优先,含 label/desc/category)"
    )


_INFER_PROMPT = """\
根据下面的故事描述,从候选题材大类和流派中选出最贴切的。

【故事描述】
{text}

【候选大类】{categories}
【候选流派】{chips}

只输出 JSON:{{"category": "大类key", "genre": "流派名(必须来自候选流派,不贴切就留空)"}}
"""


@router.post("/genre-infer", response_model=GenreInferResponse)
async def genre_infer(req: GenreInferRequest) -> GenreInferResponse:
    """推断题材:概念文本 → 大类 + 最贴流派 + 同类推荐(规则本地算,零成本换一批)。"""
    dim = _genre_dimension()
    categories = dim.get("categories") or []
    chips = dim.get("chips") or []
    if not categories:
        # 目录还是旧版扁平结构:退化为只推荐流派名
        categories = [{"key": "all", "label": "全部"}]

    cat_line = "、".join(f"{c['key']}({c['label']})" for c in categories)
    chip_line = "、".join(c["label"] for c in chips)
    prompt = _INFER_PROMPT.format(text=req.text.strip(), categories=cat_line, chips=chip_line)
    try:
        raw = await get_adapter_for(Task.SUMMARY).ask(prompt)
        data = parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"题材推断失败: {exc}") from exc

    cat_keys = {c["key"] for c in categories}
    category = str(data.get("category") or "").strip()
    genre = str(data.get("genre") or "").strip()
    chip_labels = {c["label"] for c in chips}
    if genre and genre not in chip_labels:
        genre = ""
    # 大类校验失败时,从流派反查
    if category not in cat_keys:
        hit = next((c for c in chips if c["label"] == genre), None)
        category = (hit or {}).get("category") or (categories[0]["key"] if categories else "")

    label_map = {c["key"]: c["label"] for c in categories}
    # 推荐:同大类流派优先(排除已选),不足 8 个用其他大类补
    same = [c for c in chips if c.get("category") == category and c["label"] != genre]
    others = [c for c in chips if c.get("category") != category and c["label"] != genre]
    suggestions = (same + others)[:8]
    return GenreInferResponse(
        category=category,
        category_label=label_map.get(category, ""),
        genre=genre,
        suggestions=[
            {"label": c["label"], "desc": c.get("desc", ""), "category": c.get("category", "")}
            for c in suggestions
        ],
    )
