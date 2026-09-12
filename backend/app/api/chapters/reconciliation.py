# app/api/chapters/reconciliation.py
# -*- coding: utf-8 -*-
"""交稿对账(docs/19 M3):写完一章,把系统在这章做的「自动建联」摆到作者面前。

- GET  /{chapter_number}/reconciliation  本章对账单:待确认关系边(带证据)/
       梗兑现账 / 伏笔变动。纯读。
- POST /{chapter_number}/reconciliation/confirm
       作者裁决:confirmed_relation_ids → status=confirmed;
       rejected_relation_ids → 删边并收口其证据事实(关系类事实 valid_until=n-1,
       本章新建的直接删,KnowledgeState 引用一并清)。

语义:抽取的新边 status=pending,**不拦截注入**(边本就从真实正文抽出,行为与
历史版本一致);对账给的是事后否决权——否决的边与证据事实一起退场,不污染后续。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import (
    Chapter,
    ChapterState,
    Entity,
    Foreshadowing,
    KnowledgeState,
    PremiseLedger,
    Relationship,
    User,
)
from app.db.session import get_db

router = APIRouter()


class PendingRelation(BaseModel):
    id: int
    from_name: str
    to_name: str
    relation: str
    evidence_chapter: int | None = None
    evidence_text: str = ""


class LedgerEntry(BaseModel):
    fulfilled: bool
    beat: str
    note: str
    strength: int
    evidence: str


class ForeChange(BaseModel):
    description: str
    op: str  # planted / reinforced / payoff


class ReconciliationOut(BaseModel):
    chapter_number: int
    pending_relations: list[PendingRelation] = []
    ledger: LedgerEntry | None = None
    foreshadow_changes: list[ForeChange] = []
    confirmed: bool = False  # 本章是否已无待确认项


class ConfirmIn(BaseModel):
    confirmed_relation_ids: list[int] = []
    rejected_relation_ids: list[int] = []


@router.get("/{chapter_number}/reconciliation", response_model=ReconciliationOut)
def get_reconciliation(
    chapter_number: int,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pending_rows = (
        db.query(Relationship)
        .filter(
            Relationship.project_id == project_id,
            Relationship.valid_from == chapter_number,
            Relationship.status == "pending",
        )
        .all()
    )
    name_of = {e.id: e.name for e in db.query(Entity).filter(Entity.project_id == project_id).all()}

    pending: list[PendingRelation] = []
    for e in pending_rows:
        ev_chapter: int | None = None
        ev_text = ""
        if e.evidence_fact_id:
            from app.db.models import Fact

            f = db.get(Fact, e.evidence_fact_id)
            if f is not None:
                ev_chapter = f.source_chapter
                ev_text = f.content
        pending.append(
            PendingRelation(
                id=e.id,
                from_name=name_of.get(e.from_entity_id, "?"),
                to_name=name_of.get(e.to_entity_id, "?"),
                relation=e.relation,
                evidence_chapter=ev_chapter,
                evidence_text=ev_text,
            )
        )

    ledger_row = (
        db.query(PremiseLedger)
        .filter(
            PremiseLedger.project_id == project_id,
            PremiseLedger.chapter_number == chapter_number,
        )
        .first()
    )
    ledger = (
        LedgerEntry(
            fulfilled=ledger_row.fulfilled, beat=ledger_row.beat, note=ledger_row.note,
            strength=ledger_row.strength, evidence=ledger_row.evidence,
        )
        if ledger_row
        else None
    )

    fors = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .all()
    )
    changes: list[ForeChange] = []
    for f in fors:
        if f.chapter_planted == chapter_number:
            changes.append(ForeChange(description=f.description, op="planted"))
        if f.payoff_chapter == chapter_number:
            changes.append(ForeChange(description=f.description, op="payoff"))
        if chapter_number in (f.reinforcement_chapters or []):
            changes.append(ForeChange(description=f.description, op="reinforced"))

    return ReconciliationOut(
        chapter_number=chapter_number,
        pending_relations=pending,
        ledger=ledger,
        foreshadow_changes=changes,
        confirmed=not pending,
    )


@router.post("/{chapter_number}/reconciliation/confirm")
def confirm_reconciliation(
    chapter_number: int,
    project_id: int,
    req: ConfirmIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """作者裁决:确认的边转正;否决的边删除并收口其证据事实。"""
    qs = (
        db.query(Relationship)
        .filter(
            Relationship.project_id == project_id,
            Relationship.valid_from == chapter_number,
            Relationship.status == "pending",
        )
    )
    by_id = {r.id: r for r in qs.all()}

    confirmed = 0
    for rid in req.confirmed_relation_ids:
        r = by_id.get(rid)
        if r is not None:
            r.status = "confirmed"
            confirmed += 1

    rejected = 0
    for rid in req.rejected_relation_ids:
        r = by_id.get(rid)
        if r is None:
            continue
        # 证据事实一并收口:本章新立的直接删(它就是这条判断本身);
        # 更早章留下的(罕见)关闭区间即可,引用它的 KnowledgeState 先清(SQLite 不级联)
        if r.evidence_fact_id:
            from app.db.models import Fact

            f = db.get(Fact, r.evidence_fact_id)
            if f is not None:
                if f.source_chapter and f.source_chapter < chapter_number:
                    f.valid_until = chapter_number - 1
                else:
                    (
                        db.query(KnowledgeState)
                        .filter(KnowledgeState.fact_id == f.id)
                        .delete(synchronize_session=False)
                    )
                    db.delete(f)
        db.delete(r)
        rejected += 1

    db.commit()
    return {"confirmed": confirmed, "rejected": rejected}
