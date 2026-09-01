# app/db/models/series.py
"""角色系列短片工坊:固定主角的 5-15 秒系列短视频,主角档案持久化、剧情按集喂入。

与情绪短片(命题驱动、条与条无关联)相反,这条线的核心是**角色资产**:
- SeriesCharacter 是持久的主角档案——定妆描述(look)是全系列一致性的锚,
  每次生成提示词时逐字硬注入,LLM 不得改写;参考图出片时丢给图生视频当人物锚。
- SeriesEpisode 是该主角名下的一集:用户只写剧情,生成一条可直接投喂
  图文生视频模型的成片提示词(篇幅自由,一两百字到上千字都收;
  轻量单条,不走三本子三选一)。

output 存 {title, prompt_cn, negative};draft → generating → done。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class SeriesCharacter(Base, TimestampMixin):
    """一个固定主角的档案(定妆描述是核心资产)。按用户隔离;独立于小说项目。"""

    __tablename__ = "series_characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # 角色名(如「小浣熊」):侧栏 Tab 的标签、提示词里的主角称呼
    name: Mapped[str] = mapped_column(String(60), default="")
    # 定妆描述:每次生成提示词逐字注入(跨集一致性的锚),AI 可代写草稿、用户可改
    look: Mapped[str] = mapped_column(Text, default="")
    # 画风方向(全站共用目录 engines/media/directions.py)
    direction: Mapped[str] = mapped_column(String(40), default="render3d")
    # 默认单集时长(自由输入 5-15 秒),建集时预填、可单集覆盖
    default_duration_s: Mapped[int] = mapped_column(Integer, default=10)
    # 氛围关键词(≤80 字,并进提示词),可空
    style_hints: Mapped[str] = mapped_column(String(160), default="", server_default="")
    # 定妆参考图:[{kind:'upload'|'url', src, note}](文生图出的定妆照,出片当人物锚)
    ref_images: Mapped[list[Any]] = mapped_column(JSON, default=list)


class SeriesEpisode(Base, TimestampMixin):
    """主角名下的一集:一段剧情输入 → 一条成片提示词。"""

    __tablename__ = "series_episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("series_characters.id", ondelete="CASCADE"), index=True
    )
    # 本集剧情(用户唯一的输入,一段话)
    plot: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[int] = mapped_column(Integer, default=10)
    # 生成结果:{title, prompt_cn, negative};手改后整段替换
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # draft(建了)→ generating(任务跑着)→ done(已出词)
    status: Mapped[str] = mapped_column(String(20), default="draft")
