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
    ChapterOrder,
    ChapterState,
    Entity,
    Foreshadowing,
    KnowledgeState,
    Outline,
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


class OrderCheck(BaseModel):
    """订单对账(docs/20 §6.1):确认订单的六单 vs 成品,确定性可比对的部分。

    纯规则判定(正文 contains + 实体名/别名匹配 + 关系边建联),零 LLM;
    节拍判定是章后链路写回订单的 beat_check,这里只读。
    """

    version: int = 0
    missed: list[str] = []        # 该来没来:必登场/在场人物,正文没写到
    uninvited: list[str] = []     # 不请自来:抽取新建人物,订单与大纲都没点名
    exit_missing: list[str] = []  # 说好退场:正文连人都没出现,退场戏无从谈起
    relations_missed: list[dict] = []  # 该变没变:订单关系变动,本章没有对应新建边
    beats: list[dict] = []        # 节拍判定明细(beat/hit/note)
    beats_judged: bool = False    # 节拍是否已判定(判定失败/未跑 = False,如实缺省)


class ReconciliationOut(BaseModel):
    chapter_number: int
    pending_relations: list[PendingRelation] = []
    ledger: LedgerEntry | None = None
    foreshadow_changes: list[ForeChange] = []
    confirmed: bool = False  # 本章是否已无待确认项
    # 确认订单才有的对账区;None = 无订单(老书/未确认,前端不渲染)
    order_check: OrderCheck | None = None


class ConfirmIn(BaseModel):
    confirmed_relation_ids: list[int] = []
    rejected_relation_ids: list[int] = []


def _name_variants(name: str, entities: list) -> list[str]:
    """人名 → 正文匹配用变体(本名+别名)。"""
    variants = {name}
    for e in entities:
        if e.name == name or name in (e.aliases or []):
            variants.add(e.name)
            for a in (e.aliases or []):
                variants.add(str(a))
    return [v for v in variants if v]


def _build_order_check(db: Session, project_id: int, chapter_number: int) -> OrderCheck | None:
    order = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
            ChapterOrder.status == "confirmed",
        )
        .first()
    )
    if order is None:
        return None
    payload = order.payload or {}
    cast = payload.get("cast") or {}
    entering = [
        str(e.get("name")).strip()
        for e in (cast.get("entering") or [])
        if isinstance(e, dict) and str(e.get("name") or "").strip()
    ]
    present = [str(n).strip() for n in (cast.get("present") or []) if str(n).strip()]
    exiting = [
        str(e.get("name")).strip()
        for e in (cast.get("exiting") or [])
        if isinstance(e, dict) and str(e.get("name") or "").strip()
    ]
    ordered_names = set(entering + present + exiting)

    chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number)
        .first()
    )
    text = chapter.final_content or "" if chapter is not None else ""

    entities = db.query(Entity).filter(Entity.project_id == project_id).all()

    def _mentioned(name: str) -> bool:
        return any(v and v in text for v in _name_variants(name, entities))

    # 该来没来 / 说好退场:正文 contains 判定(带别名);确定性优先,宁可漏报不误报
    missed = [n for n in entering + present if not _mentioned(n)]
    exit_missing = [n for n in exiting if not _mentioned(n)]

    # 不请自来:本章抽取涉及、且订单与大纲都没点名的实体(aliases 命中正文才算真到场)
    outline_row = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    planned = ordered_names | {
        str(n).strip() for n in (outline_row.characters_involved or []) if str(n).strip()
    } if outline_row is not None else ordered_names
    fact_chapters: dict[int, int] = {}
    from app.db.models import Fact

    for f in (
        db.query(Fact)
        .filter(Fact.project_id == project_id, Fact.source_chapter == chapter_number)
        .all()
    ):
        fact_chapters.setdefault(f.entity_id, chapter_number)
    uninvited: list[str] = []
    for e in entities:
        if e.id not in fact_chapters or e.retired:
            continue
        if any(v in planned for v in _name_variants(e.name, entities)):
            continue
        if any(v and v in text for v in _name_variants(e.name, entities)):
            uninvited.append(e.name)

    # 该变没变:订单关系变动,本章没有对应的新建边(按两端实体名建联判定)
    name_to_entity: dict[str, int] = {}
    for e in entities:
        for v in _name_variants(e.name, entities):
            name_to_entity.setdefault(v, e.id)
    edges = (
        db.query(Relationship)
        .filter(
            Relationship.project_id == project_id,
            Relationship.valid_from == chapter_number,
        )
        .all()
    )
    edge_pairs = {(r.from_entity_id, r.to_entity_id) for r in edges} | {
        (r.to_entity_id, r.from_entity_id) for r in edges
    }
    relations_missed: list[dict] = []
    for r in payload.get("relations") or []:
        if not isinstance(r, dict):
            continue
        a = name_to_entity.get(str(r.get("from") or "").strip())
        b = name_to_entity.get(str(r.get("to") or "").strip())
        if a is None or b is None:
            continue  # 对端还没入圣经,无从建联,不误报
        if (a, b) not in edge_pairs:
            relations_missed.append(
                {"from": r.get("from"), "to": r.get("to"), "after": r.get("after") or ""}
            )

    beats = [
        {"beat": b.get("beat"), "hit": bool(b.get("hit")), "note": str(b.get("note") or "")}
        for b in (order.beat_check or [])
        if isinstance(b, dict)
    ]
    return OrderCheck(
        version=order.version,
        missed=missed,
        uninvited=uninvited,
        exit_missing=exit_missing,
        relations_missed=relations_missed,
        beats=beats,
        beats_judged=order.beat_check is not None,
    )


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

    # ---- 订单对账(docs/20 §6.1):确认订单存在才算,其余章 None 零影响 ----
    order_check = _build_order_check(db, project_id, chapter_number)

    return ReconciliationOut(
        chapter_number=chapter_number,
        pending_relations=pending,
        ledger=ledger,
        foreshadow_changes=changes,
        confirmed=not pending,
        order_check=order_check,
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
