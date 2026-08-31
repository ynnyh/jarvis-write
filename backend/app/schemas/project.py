# app/schemas/project.py
# -*- coding: utf-8 -*-
"""项目与流水线接口的请求/响应模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.canon import StoryCanon
from app.schemas.concept import Concept
from app.schemas.dna import StoryDNA
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
    # 坐标卡捏出的故事 DNA / 本书基因(可空;定味道锚,治题材/口味漂移)
    dna: StoryDNA | None = None
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
    # 连写前置:True=严格模式(上一章 approved 才能连写下一章),False=宽松(默认)
    queue_require_approved: bool = False
    # 完本标记:True=已完本。完本后重命名/删除为置灰与后端拦截状态,防误删误改。
    finished: bool = False
    global_tendency: dict[str, Any]
    concept: Concept | None = None
    dna: StoryDNA | None = None
    # 故事宪法(留白/常驻装置/倒计时):全书恒真声明,注入生成+门禁;可空(老项目无)
    canon: StoryCanon | None = None
    synopsis: str | None = None
    setup_state: str | None = None
    chat_log: list[Any] | None = None
    # 卷纲(滚动规划指南针,长书才有):[{start, end, goal}]
    macro_plan: list[Any] | None = None
    # 文风备忘(随书累积的文风基线;可在翻新面板手动查看/编辑)
    style_memo: str | None = None
    # 世界观硬规则(钉板):不可违背的设定/常识,逐行一条;注入生成各环节,
    # 并可发起「规则扫描」逐章体检正文
    world_rules: str | None = None
    # 出片模式(docs/adr/0003):lite=轻量档(文+图出片)/ full=完整档(对白链/接力/合成)
    render_mode: str = "lite"
    status: str
    # 列表页进度(list 接口聚合填充;详情接口为 0)
    written_chapters: int = 0
    total_words: int = 0
    # 架构状态(只读派生,见 Project 模型的 property):是否已有架构 / 架构是否仍挂在旧概念上
    has_architecture: bool = False
    architecture_stale: bool = False
    # 架构已重写、但大纲仍挂在旧架构上(True=建议重铺蓝图或清空重来)
    outline_stale: bool = False

    model_config = {"from_attributes": True}


class ArchitectureOut(BaseModel):
    core_seed: str
    character_dynamics: str
    world_building: str
    plot_architecture: str
    version: int
    # 概念变更后置 True(架构仍挂在旧概念上);重新生成架构后复位 False
    concept_stale: bool = False

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
    beats: list[Any] = []
    current_version: int

    model_config = {"from_attributes": True}

    @field_validator("characters_involved", "key_items", "beats", mode="before")
    @classmethod
    def _null_list_to_empty(cls, v: Any) -> Any:
        # 场景节拍上线前的老行这些 JSON 列是 NULL;from_attributes 下字段存在值为
        # None 时字段默认值不生效,beats 走默认 [] 的兜底是假的,序列化直接 500
        return [] if v is None else v


class GenerateBlueprintRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)
    # 章节标题风格(Pillar 2):预设 key(plain/hook/suspense/poetic)+ 可选自由文本,
    # 由后端 resolve_title_directive 解析成一句导向注入蓝图 prompt。空=默认朴素档。
    title_style: str = Field(default="", max_length=20)
    title_directive: str = Field(default="", max_length=500)


class GenerateBlueprintResponse(BaseModel):
    outlines: list[OutlineOut]
    warnings: list[str] = Field(default_factory=list)
