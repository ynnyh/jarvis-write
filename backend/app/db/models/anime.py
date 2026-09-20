# app/db/models/anime.py
"""动画短剧工坊:固定卡司的 60-90 秒原创系列动画,类型自选、按集出梗出提示词。

产品心智沿用角色系列线的「角色是资产,剧情是耗材」,并向前一步:
- AnimeSeries.cast 是系列级卡司资产(1 主角 + 2-3 配角):主角全季固定是硬规则,
  引擎每集出梗/分镜只能用既定卡司;角色可 locked(人工调过的形象批量重出不覆盖)。
  定妆描述(appearance/wardrobe)是跨集一致性的锚,生成提示词时逐字硬注入。
- AnimeEpisode 是一集:**对话式确认流**——用户的点子先和 AI 多轮聊(点子→AI 补充
  完善成简介→用户改→再完善),用户确认简介(synopsis_ok)之后才展开分镜;没点子的
  走「三选一梗纲」捷径,选定梗纲即确认简介。分镜(shots,每镜 2-5 秒,内嵌 JSON
  不另建表)→ 整集分段提示词(film_prompt,超过外部模型单次生成上限必然切段,
  复用镜头边界贪心)。每集独立成梗,不搞前后连续。
- 只产内容与提示词,不接本站出片/合成链(产品边界,用户拍板)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class AnimeSeries(Base, TimestampMixin):
    """一个动画短剧系列:卡司 + 类型 + 画风,底下挂集。按用户隔离。"""

    __tablename__ = "anime_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(120), default="")
    # 一句话设定(这个系列讲什么/主角是谁),卡司与出梗的源头原料
    premise: Mapped[str] = mapped_column(Text, default="")
    # 类型 key(engines/anime/common.py 的 ANIME_GENRES:搞笑/热血/温情/悬疑/脑洞/日常)
    genre: Mapped[str] = mapped_column(String(40), default="comedy")
    # 画风方向(全站共用目录 engines/media/directions.py;搞笑类默认 chibi)
    direction: Mapped[str] = mapped_column(String(40), default="chibi")
    # 画风锚(风格卡一句话,生成时逐字注入;建系列时按方向预生成,可手改)
    style_cn: Mapped[str] = mapped_column(Text, default="")
    # 卡司:[{name, role:'主角'|'配角', appearance, wardrobe, personality,
    #        catchphrase, locked}]——主角恰好 1 个,配角 0-3 个;locked 字段重出不覆盖
    cast: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 每集时长档(秒):60 / 90(可扩)
    episode_s: Mapped[int] = mapped_column(Integer, default=60)
    # cast_empty(还没卡司)→ cast_ready → active(有集)
    status: Mapped[str] = mapped_column(String(20), default="cast_empty")


class AnimeEpisode(Base, TimestampMixin):
    """一集:命题 → 三梗纲 → 选定 → 分镜 → 整集分段提示词。"""

    __tablename__ = "anime_episodes"
    __table_args__ = (UniqueConstraint("series_id", "seq", name="uq_anime_ep_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    series_id: Mapped[int] = mapped_column(
        ForeignKey("anime_series.id", ondelete="CASCADE"), index=True
    )
    # 集序号(展示与排序用,1 起)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(60), default="")
    # 情境命题(用户给的一句话;空=让 AI 按类型自拟)
    premise: Mapped[str] = mapped_column(Text, default="")
    # 对话式简介:用户点子和 AI 多轮讨论的线程([{role:'user'|'assistant', content}])
    chat: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 本集简介:聊天打磨出的梗概(用户确认后才展开分镜;选梗纲也会由梗纲生成简介)
    synopsis: Mapped[str] = mapped_column(Text, default="")
    # 简介是否已确认:1=用户拍板,分镜按钮才解锁
    synopsis_ok: Mapped[int] = mapped_column(Integer, default=0)
    # 三个梗纲:[{logline, beats(节奏列表,按类型的节奏库展开), punchline}]
    takes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 选中序号(-1 未选)
    chosen: Mapped[int] = mapped_column(Integer, default=-1)
    # 分镜:[{seq, shot_type, camera, duration_s, action_desc, dialogue, speaker,
    #         characters:[名字], sfx}]——台词/动作全开(音频原生模型直接生成语音)
    shots: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 整集分段提示词文档(分段式:每段 ≤ 单次生成上限,逐段复制贴外部模型)
    film_prompt: Mapped[str] = mapped_column(Text, default="")
    # premise(建了)→ takes_ready(梗纲已出)→ synopsis_ready(简介已确认)
    # → shots_ready(分镜已出)→ prompted(提示词已出)
    status: Mapped[str] = mapped_column(String(20), default="premise")
