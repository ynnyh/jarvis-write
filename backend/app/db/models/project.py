# app/db/models/project.py
"""小说项目 + 顶层架构(雪花写作法产出)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    """一部小说。global_tendency 存全局倾向标签组合(见 04-tag-system)。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 归属用户(阶段 8 多用户隔离);存量数据迁移时归到 admin
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(100), default="")
    target_chapters: Mapped[int] = mapped_column(Integer, default=30)
    target_words_per_chapter: Mapped[int] = mapped_column(Integer, default=3000)
    # 字数守卫:finalize 后检查字数,超标则压缩/拆章。默认关闭(用户反馈约束太严),
    # 由写作页开关控制;存量项目由迁移统一关掉。
    word_guard_enabled: Mapped[bool] = mapped_column(default=False)
    word_guard_ratio: Mapped[float] = mapped_column(default=1.5)
    auto_split_enabled: Mapped[bool] = mapped_column(default=False)
    # 编辑部审校把关(生成时):定稿后自动跑校对(硬伤自修)+ 主审打分,不达标则
    # 带主审意见自动回炉重走草稿+定稿,有上限。与字数守卫同类的按项目生成偏好。
    # 阈值=四维(情节/文笔/节奏/人物)均需 >= 此值才算达标,默认 7,用户可调到 8/9。
    review_pass_threshold: Mapped[int] = mapped_column(Integer, default=7)
    review_auto_revise: Mapped[bool] = mapped_column(default=True)
    review_max_revisions: Mapped[int] = mapped_column(Integer, default=3)
    # 连写前置(docs/08 §5.5):True=严格模式,队列中下一章生成前要求上一章
    # 已人工审核通过(approved),否则队列暂停;False(默认,宽松)= 仅 quarantined 暂停。
    queue_require_approved: Mapped[bool] = mapped_column(default=False)
    # 全局倾向:标签组合 JSON,如 {"pace": "快节奏", "tone": ["热血"], ...}
    global_tendency: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 结构化故事概念(灵感工坊产出):logline/hook/twist/protagonist/conflict/setting
    # 六字段 JSON,喂养架构生成的核心种子;可空(老项目只有 topic 一句话)。
    # 见 app/schemas/concept.py。topic 保留为 logline 的镜像,下游 title/简介仍读 topic。
    concept: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 故事 DNA(创作坐标 / 本书基因):概念之上的「定味道」锚,治题材/口味漂移。
    # comps 参照系 / mode 题材模式 / axes 味道轴 / must·must_not 必须有·绝不能有 /
    # vibe 自备范本 / capsule 蒸馏基因。贯穿概念→脊柱→正文强位注入 + 双向治漂门。
    # 见 app/schemas/dna.py。可空(老项目无 DNA,生成回落到题材边界软约束)。
    dna: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 故事宪法(Canon):书级恒真「声明」——刻意留白/常驻装置/倒计时。治长程一致性里
    # 「窄窗机制够不着的恒真事实」(大院留白→第8章冒仆役、系统多章消失、倒计时算不清)。
    # 与 world_rules(自由文本)在 engines/common.constitution_block 合并成同一「宪法块」,
    # 全程注入生成 + 全程门禁比对。可空(老项目无,行为回落旧版)。见 app/schemas/canon.py。
    canon: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 书籍简介(网文风格 150-300 字,可 AI 生成也可手改);老库由迁移补列
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 起步流进度:创建即建草稿,记录停在哪一步(idea/tone/title/scale/launch);
    # 空/NULL = 起步完成(老项目天然视为完成)。列表页据此显示"继续创建"。
    setup_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 灵感对话记录([{role, content}, ...]):对话式捏概念的持久化,刷新不丢
    chat_log: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # 卷纲(滚动规划的"指南针"):[{start, end, goal}, ...]。长书蓝图只铺当前卷,
    # 写到卷尾再按卷纲+已成文状态展开下一卷;短书(≤阈值)为 NULL,一次铺完。
    macro_plan: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # 文风备忘(随书累积):一段紧凑的"本书调性 + 各主角说话特点 + 复现意象"备忘,
    # 每写完一章由快模型增量更新,注入后续章节草稿,防长篇后段人物声音漂移、调性变淡。
    # 空/NULL = 尚未累积(开篇几章)。见 prompts/chapter.py STYLE_MEMO_UPDATE_PROMPT。
    style_memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 世界观硬规则(钉板):用户手填的"本书不可违背的设定/常识"(如"2024 新高考,
    # 理科不考政治,高考 6.7-6.8 两天"),逐行一条。注入蓝图/草稿/定稿等生成环节
    # (见 engines/common.world_rules_block),并可发起「规则扫描」逐章体检正文
    # (见 engines/diagnosis.rule_scan_book)。空/NULL = 未设置。
    world_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    # draft / outlining / writing / done
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 出片模式(docs/adr/0003):lite=轻量档(文+图出片,逐镜手动筛选),
    # full=完整档(对白配音链/自动接力/一键合成)。存量项目默认 lite;
    # 完整档模块分期点亮,未点亮时前端按「未启用」占位展示。
    render_mode: Mapped[str] = mapped_column(String(10), default="lite", server_default="lite")
    # 架构已重写、但大纲仍挂在旧架构上 → True:前端大纲页据此告知「保留或清空重来」。
    # 与 Architecture.concept_stale 同一模式,但作用于大纲整组(架构一变影响全部章节蓝图)。
    # 重新铺蓝图(save_blueprint)自动复位 False。存量行为零变化(默认 False)。
    outline_stale: Mapped[bool] = mapped_column(Boolean, default=False)

    architecture: Mapped["Architecture | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )

    # 只读派生属性,供 ProjectOut 透出(前端据此做「已有架构?」「需重生成?」判断)
    @property
    def has_architecture(self) -> bool:
        return self.architecture is not None

    @property
    def architecture_stale(self) -> bool:
        return bool(self.architecture and self.architecture.concept_stale)


class Architecture(Base):
    """顶层架构:雪花写作法四步产出。可改,故带 version。"""

    __tablename__ = "architecture"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    core_seed: Mapped[str] = mapped_column(Text, default="")
    character_dynamics: Mapped[str] = mapped_column(Text, default="")
    world_building: Mapped[str] = mapped_column(Text, default="")
    plot_architecture: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 概念变更后,旧架构仍挂在旧概念上(True=「基于旧概念,建议重新生成架构」)。
    # 重新生成架构(save_architecture)时自动复位 False。详见 app/engines/pipeline/architecture.py
    concept_stale: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="architecture")
