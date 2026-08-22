# app/db/models/chapter_state.py
"""章末交接契约:每章定稿后提取的章末瞬态(时间/地点/人物即时状态/未决线索)。

设计见 docs/08 §5.2。圣经(facts)只记跨章持久事实,不记"章末那一刻人在哪、
几点、什么状态"——本表补这个缺口,供下一章开头衔接注入与后续连续性门禁比对。

一章一条当前契约(chapter_id 唯一),不版本化:重写章节时 purge 旧契约重新提取,
旧版正文本身已由 chapter_versions 留痕,契约随当前正文走即可。
content_hash 是提取时正文指纹(与 editorial.content_hash 同算法),正文被
手改/回滚后契约自动失效(取用时校验指纹)。
提取失败也落一行(extract_status=failed + extract_error),留痕但不阻塞
主流程(docs/08 §4 可降级原则:不许静默跳过)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterState(Base, TimestampMixin):
    __tablename__ = "chapter_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), unique=True, index=True
    )
    # 契约 JSON:in_story_time / story_day / days_remaining / location /
    # scene_continues / ambient / characters[] / open_threads / devices_present /
    # time_jump_hint
    # (story_day/days_remaining 供故事时钟派生权威天数轴,见 engines/timeline.py;
    #  devices_present 供常驻装置复现驱动派生断档章数,见 engines/devices.py;
    #  结构详见 prompts/consistency.py 的 HANDOFF_CONTRACT_PROMPT)
    contract: Mapped[str] = mapped_column(Text, default="")
    # 提取所依据正文的指纹(sha256 前 16 位,同 editorial.content_hash)
    content_hash: Mapped[str] = mapped_column(String(16), default="")
    # ok / failed —— LLM 或 JSON 校验失败也落行留痕,error 记原因
    extract_status: Mapped[str] = mapped_column(String(20), default="ok")
    extract_error: Mapped[str] = mapped_column(Text, default="")
