# app/db/models/motif.py
"""桥段台账(跨章重复描写追踪)+ 雷区清单(作者明令禁止的桥段)。

治「连续章节写同一描写」:n-gram 查重(repetition.py)只抓字面重复,抓不住
「换着说法复用同一桥段」(铁锈玫瑰/扎胸膛/躺下等天亮)。本表把每章最显眼的
描写母题沉淀成结构化短标签,按标签跨章聚合;同一标签出现多次,后续章生成时
注入「已写滥,禁止再用」——语义级防复读。作者也可手动把某个桥段设为雷区,
一次标注全书生效(补齐批注跨不了章的缺口)。

一张表两种行,靠 banned/source 区分:
  - 台账行:banned=False,source="auto",每 (project, chapter, label) 一行,
    由章后抽取或全书扫描写入(重抽幂等:先清本章再插);
  - 雷区行:banned=True,source="user",(project, label) 唯一,只增删不改。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class WritingMotif(Base, TimestampMixin):
    __tablename__ = "writing_motifs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # 短标签(2-10 字),如「铁锈玫瑰」「躺下等天亮」——可检索、跨章聚合的键
    label: Mapped[str] = mapped_column(String(100), default="")
    # 一句话说明这个桥段/意象长什么样(注入 prompt 时帮模型对上号)
    detail: Mapped[str] = mapped_column(Text, default="")
    # 标签来自哪一章;0 = 非章节来源(用户手动添加的雷区)
    chapter_number: Mapped[int] = mapped_column(Integer, default=0)
    # auto = 章后抽取/全书扫描;user = 用户手动
    source: Mapped[str] = mapped_column(String(10), default="auto")
    # True = 雷区(作者明令禁止,后续所有章节不得再写);台账行恒为 False
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
