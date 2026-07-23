# app/schemas/project.py
# -*- coding: utf-8 -*-
"""项目与流水线接口的请求/响应模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.concept import Concept
from app.schemas.tendency import Tendency


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = ""
    genre: str = ""
    target_chapters: int = Field(default=30, ge=1, le=2000)
    target_words_per_chapter: int = Field(default=3000, ge=200, le=20000)
    global_tendency: Tendency = Field(default_factory=dict)
    # 新建向导第一步选定的结构化概念(可空;传入则落库并把 topic 同步为 logline)
    concept: Concept | None = None
    # 起步流:创建草稿项目时标记停在哪步(idea/tone/title/scale/launch);不传=直接完成
    setup_state: str | None = Field(default=None, max_length=20)


class ProjectOut(BaseModel):
    id: int
    title: str
    topic: str
    genre: str
    target_chapters: int
    target_words_per_chapter: int
    # 字数守卫开关(写作页):超标自动压缩/拆章,默认关闭
    word_guard_enabled: bool = False
    auto_split_enabled: bool = False
    # 编辑部审校把关(生成时自动校对+主审+有上限回炉)
    review_pass_threshold: int = 7
    review_auto_revise: bool = True
    review_max_revisions: int = 3
    global_tendency: dict[str, Any]
    concept: Concept | None = None
    synopsis: str | None = None
    setup_state: str | None = None
    chat_log: list[Any] | None = None
    # 卷纲(滚动规划指南针,长书才有):[{start, end, goal}]
    macro_plan: list[Any] | None = None
    status: str
    # 列表页进度(list 接口聚合填充;详情接口为 0)
    written_chapters: int = 0
    total_words: int = 0

    model_config = {"from_attributes": True}


class ArchitectureOut(BaseModel):
    core_seed: str
    character_dynamics: str
    world_building: str
    plot_architecture: str
    version: int

    model_config = {"from_attributes": True}


class GenerateArchitectureRequest(BaseModel):
    """生成顶层架构。倾向为单次临时值,与项目全局倾向合并。

    directive: 「架构研讨」对话蒸馏出的额外要求(可空),高优先级注入四步生成。
    """

    tendency: Tendency = Field(default_factory=dict)
    directive: str = Field(default="", max_length=2000)


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
