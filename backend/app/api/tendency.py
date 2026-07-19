# app/api/tendency.py
# -*- coding: utf-8 -*-
"""倾向标签目录接口:前端渲染 chips 用。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.engines.tendency import get_catalog, get_node_catalog
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
