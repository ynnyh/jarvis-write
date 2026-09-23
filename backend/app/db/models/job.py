# app/db/models/job.py
"""后台任务持久化(配合 jobs.py 混合存储)。

状态转换(create/finish/fail)写 DB,高频 stage 更新仅写内存。
服务重启后:running 超时的标记为 failed,已完成的保留供前端查看历史。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(10), default="running", index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(200), default="排队中")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 服务端解析出的归属(docs/20 任务中心分组):project_id/chapter_number 由 kind
    # 解析入库,前端不再正则猜;parent_job_id 预留父子任务(队列子任务等)。
    # 旧任务三列为 NULL → 前端回退平铺展示(jobLabel 正则照旧)。
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chapter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_job_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

class JobStep(Base):
    """任务步骤检查点(Phase 4.2):长任务里每个昂贵子步骤的落库留痕。

    - clips 批量:每张卡的展开(step_key = "take:{index}");
    - 场景级章节:每场的生成+验收(step_key = "scene:{seq}")。
    用途:排查「任务死在哪一步」/ 成本归因到步骤 / 断点续跑的数据底座。
    同 (job_id, step_key) 重试时覆盖(upsert),只留最新一次。
    """

    __tablename__ = "job_steps"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(12), index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    # done / failed(不记 running:步骤记录是完成时的快照,不是实时状态)
    status: Mapped[str] = mapped_column(String(10), default="done")
    # 产出摘要(小体量 JSON:场卡字数/验收结论、卡片的落定状态等;不存大文本)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
