# app/db/models/story_bible.py
"""时序故事圣经:系统心脏。

核心思想(借鉴 knowrite Temporal Truth DB + graphify 知识图谱):
事实不是静态的,而是带"有效章节区间"的。查询"第 N 章时角色 X 状态如何",
只返回 valid_from <= N <= valid_until(或 valid_until 为 null)的事实。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Entity(Base):
    """实体 = 知识图谱的"节点"(角色/地点/物品/势力)。"""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # character / location / item / faction
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # 别名列表,防止 LLM 换称呼后认不出同一实体
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    base_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Fact(Base, TimestampMixin):
    """时序事实(Temporal Truth)。

    例:角色A第5章受伤 → Fact(valid_from=5, valid_until=11);
        第12章痊愈 → 新 Fact(valid_from=12, valid_until=None)。
        查第8章 → 命中"受伤"。
    """

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    # state / ability / possession / relationship / location
    fact_type: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[int] = mapped_column(Integer, index=True)
    valid_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # critical / major / minor
    importance: Mapped[str] = mapped_column(String(10), default="major")
    source_chapter: Mapped[int] = mapped_column(Integer, default=0)


class Relationship(Base):
    """关系边 = 知识图谱的"边"。关系会变,故带时序。"""

    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    from_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    to_entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    relation: Mapped[str] = mapped_column(String(100))  # 师徒/仇敌/恋人...
    valid_from: Mapped[int] = mapped_column(Integer)
    valid_until: Mapped[int | None] = mapped_column(Integer, nullable=True)


class KnowledgeState(Base):
    """谁知道什么(借鉴 KazKozDev 读者/角色已知分离)。

    写悬疑必备:同一真相,读者第3章就知道、角色B到第10章才知道。
    生成时据此控制"角色现在不该说出他还不知道的事"。
    """

    __tablename__ = "knowledge_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), index=True
    )
    # "reader" 或 角色 entity_id 的字符串形式
    knower: Mapped[str] = mapped_column(String(50), index=True)
    known_from_chapter: Mapped[int] = mapped_column(Integer)
    # known / suspected / blind
    knower_state: Mapped[str] = mapped_column(String(10), default="known")
