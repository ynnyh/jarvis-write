# app/db/models/clips.py
"""情绪短片工坊:15/30 秒情绪命题短视频,一次产三个本子三选一。

双入口共用一套引擎:
- 通用入口(书房):source_project_id 为空,主题=情绪命题(遗憾/争吵/爱情/童趣…),纯虚构;
- 小说衍生入口(项目内):source_project_id 指向小说,从书里抽金句/名场面/角色出投流种草片,
  金句必须能在提供的正文节选里找到(引擎做子串溯源校验,找不到进 cautions)。

candidates 存三个候选本子(每个自含 logline/情绪曲线/台词/分镜含三轨提示词/金句/切段);
chosen 指向选中序号(-1 未选),clip 存选中的那本(最终态)。分镜内嵌 JSON(3-7 格,不另建表)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class MoodClip(Base, TimestampMixin):
    """一条情绪短片企划(候选三选一)。按用户隔离;可挂在某本小说下(投流短视频)。"""

    __tablename__ = "mood_clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # 小说衍生时指向源项目;通用情绪短片为空
    source_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    # 工坊类型:mood=情绪短片(主题=CLIP_THEMES),play=灵感工坊(主题=CLIPS_PLAYS 玩法)
    mode: Mapped[str] = mapped_column(String(20), default="mood", server_default="mood")
    # 主题 key(见 engines/clips/common 目录)或空=自定义
    theme: Mapped[str] = mapped_column(String(40), default="")
    custom_theme: Mapped[str] = mapped_column(String(120), default="")
    # 15 / 30 秒
    duration_s: Mapped[int] = mapped_column(Integer, default=15)
    # 画风方向(三线共用目录:engines/media/directions.py)
    direction: Mapped[str] = mapped_column(String(40), default="live")
    # 一句话灵感种子(如「异地恋的最后一通电话」),可空
    inspiration: Mapped[str] = mapped_column(Text, default="")
    # ===== 导向维度(用户可细化的"方向",auto=AI 自定;目录见 engines/clips/common.py)=====
    # 台词风格:auto/voiceover(旁白独白)/dialogue(对白主导)/silent(无台词)
    dialogue_style: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    # 节奏:auto/hook_first(爆点前置)/slow_burn(层层蓄势)/twist_end(结尾反转)
    pacing: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    # 情绪浓度:auto/restrained(克制留白)/standard/intense(浓烈直给)
    intensity: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    # 氛围关键词自由文本(≤80 字,注入风格卡:如「雨夜便利店、暖光、旧磁带」),可空
    style_hints: Mapped[str] = mapped_column(String(160), default="", server_default="")
    # ===== 风格卡(批产时一并生成,三个候选共用)=====
    style_name: Mapped[str] = mapped_column(String(60), default="")
    style_cn: Mapped[str] = mapped_column(Text, default="")
    style_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    # 三个候选本子:[{take, logline, emotion_curve, lines, shots, punchline, chunks, quote_source, cautions}]
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 选中序号(-1 未选);选中后 clip 为最终态
    chosen: Mapped[int] = mapped_column(Integer, default=-1)
    clip: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # draft(建了)→ generated(候选已出)→ picked(已选定)
    status: Mapped[str] = mapped_column(String(20), default="draft")
