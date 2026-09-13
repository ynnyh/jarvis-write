# app/api/projects/dashboard.py
# -*- coding: utf-8 -*-
"""首页驾驶舱(docs/19 中期):跨书聚合「今天该干什么」。

待对账、逾期伏笔、文扑预警、失配章分散在写作区/伏笔页/体检页——多本书的作者
没有一个地方能一眼看到全部待办。本端点按项目聚合确定性信号(零 LLM),
每项目给一条最该先做的建议(suggestion);全空闲时 suggestions 为空,
不填假待办。只读聚合,默认每用户最多回 12 本(首页展示容量)。
"""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import (
    Chapter,
    Entity,
    Foreshadowing,
    Outline,
    PremiseLedger,
    Project,
    Relationship,
    User,
)
from app.db.session import get_db

from fastapi import APIRouter, Depends

router = APIRouter()

_MAX_PROJECTS = 12


class ProjectTodo(BaseModel):
    project_id: int
    title: str
    written: int
    planned: int
    # 各信号(全确定性;0 表示该项干净,不是「没数据」)
    pending_reconciliation: int          # 有待确认关系边的章数
    overdue_foreshadows: int             # 逾期未收伏笔条数
    premise_streak: int | None = None    # 最近连续未兑现核心梗章数;未建梗卡为 None
    premise_missing: bool = False        # 已开写但梗卡未建(提示补建)
    stale_chapters: int                  # 大纲改动后失配的章数
    # 最该先做的一件事(按严重度排序取首个;空 = 无待办)
    suggestion: str | None = None
    path: str | None = None


class DashboardOut(BaseModel):
    projects: list[ProjectTodo] = []


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    projects = (
        db.query(Project)
        .filter(Project.user_id == user.id)
        .order_by(Project.id.desc())
        .limit(_MAX_PROJECTS)
        .all()
    )
    out: list[ProjectTodo] = []
    for p in projects:
        written = [
            c.chapter_number
            for c in db.query(Chapter)
            .filter(Chapter.project_id == p.id)
            .all()
            if (c.final_content or "").strip()
        ]
        written_n = len(written)
        max_ch = max(written) if written else 0

        # 待对账:pending 关系边所在章(边无章号列,用 valid_from 即建联章),
        # 去重后 = 交稿对账区还没处理完的章数
        pend_rows = (
            db.query(Relationship.valid_from)
            .filter(
                Relationship.project_id == p.id,
                Relationship.status == "pending",
            )
            .distinct()
            .all()
        )
        pending_n = len(pend_rows)

        # 逾期伏笔:预期回收章已过、仍 planted/reinforced
        overdue = (
            db.query(Foreshadowing)
            .filter(
                Foreshadowing.project_id == p.id,
                Foreshadowing.expected_payoff_chapter.isnot(None),
                Foreshadowing.expected_payoff_chapter <= max_ch,
                Foreshadowing.status.in_(("planted", "reinforced")),
            )
            .count()
        )

        # 失配章:大纲改动后正文待重写
        stale = (
            db.query(Outline)
            .join(Chapter, Chapter.outline_id == Outline.id)
            .filter(Outline.project_id == p.id, Chapter.is_stale.is_(True))
            .count()
        )

        # 梗健康:连续未兑现(从最近一章往回数账面)
        streak: int | None = None
        premise_missing = False
        from app.db.models.premise import Premise

        premise = (
            db.query(Premise)
            .filter(Premise.project_id == p.id, Premise.kind == "main")
            .first()
        )
        has_card = premise is not None and (premise.high_concept or "").strip()
        ledgers = {
            l.chapter_number: l
            for l in db.query(PremiseLedger)
            .filter(PremiseLedger.project_id == p.id)
            .all()
        }
        if written:
            if not has_card:
                premise_missing = True
            else:
                streak = 0
                for n in sorted(written, reverse=True):
                    row = ledgers.get(n)
                    if row is None or not row.fulfilled:
                        streak += 1
                    else:
                        break

        # 建议按严重度:文扑预警 > 待对账 > 逾期伏笔 > 失配 > 补建梗卡
        suggestion = path = None
        if (streak or 0) >= 3:
            suggestion = f"连续 {streak} 章未兑现核心梗,文扑预警"
            path = f"/project/{p.id}/book?tab=health"
        elif pending_n:
            suggestion = f"{pending_n} 章有待确认的关系对账"
            path = f"/project/{p.id}/write?ch={min(r[0] for r in pend_rows)}"
        elif overdue:
            suggestion = f"{overdue} 条伏笔逾期未收"
            path = f"/project/{p.id}/book?tab=foreshadow"
        elif stale:
            suggestion = f"{stale} 章正文与新版大纲失配"
            path = f"/project/{p.id}/book?tab=overview"
        elif premise_missing:
            suggestion = "已开写但未建核心梗卡,建议补建"
            path = f"/project/{p.id}/settings"
        elif written_n == 0 and p.setup_state in (None, "") and (
            db.query(Outline).filter(Outline.project_id == p.id).count()
        ):
            # 蓝图铺好但一字未写:推一把「开始写」;纯空书不给假待办(新手轨清单负责)
            suggestion = "蓝图已就绪,从第 1 章开始写"
            path = f"/project/{p.id}/write?ch=1"

        out.append(
            ProjectTodo(
                project_id=p.id, title=p.title, written=written_n,
                planned=p.target_chapters,
                pending_reconciliation=pending_n, overdue_foreshadows=overdue,
                premise_streak=streak, premise_missing=premise_missing,
                stale_chapters=stale, suggestion=suggestion, path=path,
            )
        )
    return DashboardOut(projects=out)
