# app/db/models/chapter_issue.py
"""章节一致性问题清单:写后一致性门禁(Continuity Gate)等来源产出的问题记录。

设计见 docs/08 §5.4/§5.7。每条问题带 问题点/证据段落/修正建议/严重程度/类型,
状态机 open → resolved / ignored:
  - open:本轮检查发现、尚未处理
  - resolved:人工改完标记已解决(P1 面板用)
  - ignored:用户确认忽略(放行);按正文指纹判断是否仍适用——正文重写后
    指纹变化,旧 ignored 记录不再生效(门禁重建时清除),同一矛盾会重新报警。

幂等:每次门禁检查落库时 purge 本章旧 open 记录、按当前结果重建(对齐
extractor/handoff 的清旧账模式);一章的 open 集永远只反映最近一次检查。
content_hash 是检查时正文指纹(与 editorial.content_hash 同算法)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterIssue(Base, TimestampMixin):
    __tablename__ = "chapter_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    # 来源:gate(写后一致性门禁)/ preflight(写前审核,P1)/ diag(离线诊断,P2)/
    # review(审校)/ rules(规则扫描,对照世界观硬规则钉板)
    source: Mapped[str] = mapped_column(String(20), default="gate")
    # blocker(硬矛盾,阻断)/ major / minor
    severity: Mapped[str] = mapped_column(String(20), default="minor")
    # state / knowledge / timeline / worldrule
    issue_type: Mapped[str] = mapped_column(String(20), default="state")
    description: Mapped[str] = mapped_column(Text, default="")
    # 证据段落:本章正文里的逐字引用(引擎已过滤引不到原文的幻觉举证)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    # open / resolved / ignored
    status: Mapped[str] = mapped_column(String(20), default="open")
    # 本次检查所依据正文的指纹(sha256 前 16 位,同 editorial.content_hash)
    content_hash: Mapped[str] = mapped_column(String(16), default="")
