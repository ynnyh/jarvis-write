# app/db/models/skill_pack.py
"""创作 Skill 包:官方内置或作者自建的可插拔创作约束包(docs/21)。

一包 = 「scope 适用线 + entries 条目集」:条目声明注入节点(node)与形态
(kind:directive 指令 / param 参数 / ban 排除清单 / format 渲染工艺),由
engines/skills/packs.py 按节点过滤、按预算闸注入生成链。

与 tendency 体系(倾向标签/手法卡)的分工:tendency 是「每本书勾什么生效」的
散装约束;Skill 包是「成套、随版本分发的工艺」。可开关、可编辑、版本化
(history 保留最近 HISTORY_KEEP 版可回退);is_builtin 只影响「随包分发」,
不锁编辑——官方包同样可改可关(docs/21 风险 8:不许长成「写死审美 2.0」)。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class SkillPack(Base, TimestampMixin):
    __tablename__ = "skill_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 稳定键:seed 幂等与引擎挂载(镜头卡工艺开关)都认它,不认自增 id
    pack_key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    # 适用线:anime / series / drama / clips / novel / inspire
    scope: Mapped[list] = mapped_column(JSON, default=list)
    # 条目:[{node, kind, directive, params, ban_list}]——node 是注入节点;
    # kind=format 的条目不进 prompt,只作渲染工艺的开关信号
    entries: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 编辑历史:[{version, entries}],最新在尾,保留 HISTORY_KEEP 版
    history: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
