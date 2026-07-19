# app/engines/consistency/foreshadow.py
# -*- coding: utf-8 -*-
"""伏笔调度器(借鉴 NovelClaw 四态 + KazKozDev 揭示调度)。

四态:planted(埋)→ reinforced(强化)→ paid_off(回收)/ abandoned(弃)。
调度规则:未回收 且 expected_payoff_chapter <= 当前章+2 → 进"该回收"提醒。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Foreshadowing

logger = logging.getLogger("jarvis-write.foreshadow")

_DUE_WINDOW = 2  # 距预期回收章还有几章时开始提醒


class ForeshadowScheduler:
    def __init__(self, db: Session, project_id: int):
        self.db = db
        self.project_id = project_id

    def open_foreshadowings(self) -> list[Foreshadowing]:
        """所有未回收未弃用的伏笔。"""
        return (
            self.db.query(Foreshadowing)
            .filter(
                Foreshadowing.project_id == self.project_id,
                Foreshadowing.status.in_(("planted", "reinforced")),
            )
            .order_by(Foreshadowing.chapter_planted)
            .all()
        )

    def due_foreshadowings(self, chapter_number: int) -> list[Foreshadowing]:
        """该回收的伏笔:预期回收章临近或已过。"""
        return [
            f
            for f in self.open_foreshadowings()
            if f.expected_payoff_chapter is not None
            and f.expected_payoff_chapter <= chapter_number + _DUE_WINDOW
        ]

    def reminder_block(self, chapter_number: int) -> str:
        """渲染 Prompt 提醒块。"""
        due = self.due_foreshadowings(chapter_number)
        if not due:
            return "(暂无到期伏笔)"
        lines = []
        for f in due:
            overdue = (
                f.expected_payoff_chapter is not None
                and f.expected_payoff_chapter < chapter_number
            )
            mark = "⚠️已逾期" if overdue else "⏰临近"
            lines.append(
                f"[{mark}] {f.description}"
                f"(第{f.chapter_planted}章埋设,预期第{f.expected_payoff_chapter}章回收)"
            )
        return "\n".join(lines)

    def _find_by_description(self, description: str) -> Foreshadowing | None:
        description = description.strip()
        if not description:
            return None
        for f in (
            self.db.query(Foreshadowing)
            .filter(Foreshadowing.project_id == self.project_id)
            .all()
        ):
            # 抽取器要求 reinforce/payoff 抄原文;稍作宽容:包含即匹配
            if f.description == description or (
                len(description) > 6
                and (description in f.description or f.description in description)
            ):
                return f
        return None

    def apply_ops(self, chapter_number: int, ops: list[dict]) -> dict:
        """把章后抽取的伏笔操作写库。"""
        stats = {"planted": 0, "reinforced": 0, "paid_off": 0, "skipped": 0}
        for op in ops or []:
            kind = (op.get("op") or "").strip()
            desc = (op.get("description") or "").strip()
            if not desc:
                stats["skipped"] += 1
                continue

            if kind == "plant":
                if self._find_by_description(desc) is not None:
                    stats["skipped"] += 1  # 已存在,防重复
                    continue
                self.db.add(
                    Foreshadowing(
                        project_id=self.project_id,
                        description=desc,
                        chapter_planted=chapter_number,
                        expected_payoff_chapter=op.get("expected_payoff_chapter"),
                        status="planted",
                        importance=op.get("importance") or "major",
                        reinforcement_chapters=[],
                    )
                )
                stats["planted"] += 1
            elif kind == "reinforce":
                f = self._find_by_description(desc)
                if f is None or f.status not in ("planted", "reinforced"):
                    stats["skipped"] += 1
                    continue
                f.status = "reinforced"
                f.reinforcement_chapters = list(f.reinforcement_chapters or []) + [
                    chapter_number
                ]
                stats["reinforced"] += 1
            elif kind == "payoff":
                f = self._find_by_description(desc)
                if f is None or f.status == "paid_off":
                    stats["skipped"] += 1
                    continue
                f.status = "paid_off"
                f.payoff_chapter = chapter_number
                stats["paid_off"] += 1
            else:
                stats["skipped"] += 1

        self.db.flush()
        logger.info("伏笔操作(第%d章): %s", chapter_number, stats)
        return stats
