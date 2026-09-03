# app/db/models/mark.py
"""章节标记:作者在正文里随手记下的「这里不行」——跨章持久化的批注。

治「批注是内存状态,切章/刷新即丢」:标记落库后可以边读边攒,攒够后在 AI 栏
一句总描述驱动「全书批修」(逐标记锁情节改写,逐条 diff 验收),补齐
「单章批注改」跨不了章的缺口。

身份与失效判定与内存版批注完全一致:para_idx + snapshot(原文快照);
正文被改动后快照对不上 = 失效,批修时跳过并在结果里说明。
status:open(待处理)/ fixed(批修已接受,销账;由前端验收后经 DELETE 落实,
服务端不主动改——批修 job 本身不落库,只产出待验收的替换对)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterMark(Base, TimestampMixin):
    __tablename__ = "chapter_marks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    # 段落下标(与前端 splitParas 同口径:\n 分段、trim、去空行)
    para_idx: Mapped[int] = mapped_column(Integer, default=0)
    # 记标记时的段落原文快照(正文变动后据此判失效)
    snapshot: Mapped[str] = mapped_column(Text, default="")
    # 一句话意见(这处为什么不行/想要什么)
    note: Mapped[str] = mapped_column(Text, default="")
    # open / fixed
    status: Mapped[str] = mapped_column(String(10), default="open")
