# app/schemas/project.py
# -*- coding: utf-8 -*-
"""项目与流水线接口的请求/响应模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.tendency import Tendency


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = ""
    genre: str = ""
    target_chapters: int = Field(default=30, ge=1, le=2000)
    target_words_per_chapter: int = Field(default=3000, ge=200, le=20000)
    global_tendency: Tendency = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: int
    title: str
    topic: str
    genre: str
    target_chapters: int
    target_words_per_chapter: int
    global_tendency: dict[str, Any]
    status: str

    model_config = {"from_attributes": True}


class ArchitectureOut(BaseModel):
    core_seed: str
    character_dynamics: str
    world_building: str
    plot_architecture: str
    version: int

    model_config = {"from_attributes": True}


class GenerateArchitectureRequest(BaseModel):
    """生成顶层架构。倾向为单次临时值,与项目全局倾向合并。"""

    tendency: Tendency = Field(default_factory=dict)


class OutlineOut(BaseModel):
    id: int
    chapter_number: int
    title: str
    chapter_role: str
    chapter_purpose: str
    suspense_level: str
    foreshadowing: str
    plot_twist_level: str
    summary: str
    characters_involved: list[Any]
    key_items: list[Any]
    scene_location: str
    current_version: int

    model_config = {"from_attributes": True}


class GenerateBlueprintRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)


class GenerateBlueprintResponse(BaseModel):
    outlines: list[OutlineOut]
    warnings: list[str] = Field(default_factory=list)
