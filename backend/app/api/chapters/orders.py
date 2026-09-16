# app/api/chapters/orders.py
# -*- coding: utf-8 -*-
"""章节订单(docs/20 订单制):写前确认单的存取与确认。

- GET    /{chapter_number}/order          当前订单 + 写前预填(纯确定性投影)
- PUT    /{chapter_number}/order          保存草稿(存草稿即回 draft,不参与生成)
- POST   /{chapter_number}/order/confirm  确认订单(version+1,旧版归档 history)
- POST   /{chapter_number}/order/unconfirm 撤回确认(回 draft,保留内容)
- DELETE /{chapter_number}/order          删除订单(回到蓝图行生成,存量行为)

预填零抽取:人物/节拍来自大纲,承上钩子来自上一章章末契约的 open_threads,
伏笔带出到期未收项——与本章作战图同一套数据源,两处不会各说各话。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import (
    Chapter,
    ChapterOrder,
    ChapterState,
    Foreshadowing,
    Outline,
    User,
)
from app.db.session import get_db

router = APIRouter()

# 历史版本保留上限:订单是轻量意图记录,无限归档只会膨胀 JSON
_HISTORY_KEEP = 10

# 六单的合法键:多出来的键直接丢弃,防脏 payload 流进提示词渲染
_PAYLOAD_KEYS = {"cast", "relations", "beats", "hooks", "foreshadow", "scenes", "free_directive"}


def _normalize_payload(raw: dict) -> dict:
    """把前端送来的 payload 规整成六单结构;脏形状就地清洗,不抛错。"""
    out: dict[str, Any] = {k: raw.get(k) for k in _PAYLOAD_KEYS if k in raw}
    cast = out.get("cast")
    if isinstance(cast, dict):
        out["cast"] = {
            "entering": cast.get("entering") if isinstance(cast.get("entering"), list) else [],
            "present": cast.get("present") if isinstance(cast.get("present"), list) else [],
            "exiting": cast.get("exiting") if isinstance(cast.get("exiting"), list) else [],
        }
    if not isinstance(out.get("relations"), list):
        out["relations"] = []
    if not isinstance(out.get("beats"), list):
        out["beats"] = []
    hooks = out.get("hooks")
    out["hooks"] = hooks if isinstance(hooks, dict) else {}
    fore = out.get("foreshadow")
    out["foreshadow"] = fore if isinstance(fore, dict) else {}
    if not isinstance(out.get("scenes"), list):
        out["scenes"] = []
    if not isinstance(out.get("free_directive"), str):
        out["free_directive"] = ""
    return out


def _load_contract(db: Session, project_id: int, chapter_number: int) -> dict:
    """上一章章末契约 JSON(脏契约当没有——与 dossier 同款纪律)。"""
    row = (
        db.query(ChapterState)
        .join(Chapter, ChapterState.chapter_id == Chapter.id)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if row is None or not row.contract:
        return {}
    try:
        data = json.loads(row.contract)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _prefill(db: Session, project_id: int, outline: Outline) -> dict:
    """写前预填:大纲 + 上章契约 + 到期伏笔,纯确定性,零 LLM。"""
    prev_contract = _load_contract(db, project_id, outline.chapter_number - 1)
    threads = prev_contract.get("open_threads")
    carry_in = (
        [{"text": str(t), "must": False} for t in threads if str(t).strip()]
        if isinstance(threads, list)
        else []
    )
    due = (
        db.query(Foreshadowing)
        .filter(
            Foreshadowing.project_id == project_id,
            Foreshadowing.status == "planted",
            Foreshadowing.expected_payoff_chapter.isnot(None),
            Foreshadowing.expected_payoff_chapter <= outline.chapter_number,
        )
        .all()
    )
    return {
        "cast": {
            "entering": [],
            "present": [str(n) for n in (outline.characters_involved or []) if str(n).strip()],
            "exiting": [],
        },
        "relations": [],
        "beats": [str(b) for b in (outline.beats or []) if str(b).strip()],
        "hooks": {"carry_in": carry_in, "leave": ""},
        "foreshadow": {
            "due": [{"id": f.id, "description": f.description} for f in due],
            "plant": [],
        },
        "scenes": [],
        "free_directive": "",
    }


def _order_out(order: ChapterOrder) -> dict:
    return {
        "id": order.id,
        "chapter_number": order.chapter_number,
        "payload": order.payload or {},
        "status": order.status,
        "version": order.version,
        "beat_check": order.beat_check,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


@router.get("/{chapter_number}/order")
def get_order(
    chapter_number: int,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前订单 + 预填。没有订单时 order 为 null,前端用 prefill 起草。"""
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == chapter_number)
        .first()
    )
    if outline is None:
        return {"order": None, "prefill": None}
    order = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
        )
        .first()
    )
    return {
        "order": _order_out(order) if order else None,
        "prefill": _prefill(db, project_id, outline),
    }


@router.put("/{chapter_number}/order")
def save_order(
    chapter_number: int,
    project_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存草稿。已确认的订单再保存即回 draft(改了主意就要重新拍板)。"""
    order = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
        )
        .first()
    )
    clean = _normalize_payload(payload)
    if order is None:
        order = ChapterOrder(
            project_id=project_id,
            chapter_number=chapter_number,
            payload=clean,
            status="draft",
            version=1,
        )
        db.add(order)
    else:
        order.payload = clean
        order.status = "draft"
        order.beat_check = None  # 订单变了,旧判定作废
    db.commit()
    return _order_out(order)


@router.post("/{chapter_number}/order/confirm")
def confirm_order(
    chapter_number: int,
    project_id: int,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """确认订单(可携带最终 payload 一并落库)。version+1,上一版归档 history。"""
    order = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
        )
        .first()
    )
    if order is None:
        order = ChapterOrder(
            project_id=project_id, chapter_number=chapter_number, version=1
        )
        db.add(order)
    if isinstance(payload, dict) and payload:
        order.payload = _normalize_payload(payload)
    if order.status != "confirmed" and order.payload:
        history = list(order.history or [])
        history.append(
            {
                "version": order.version,
                "payload": order.payload,
                "confirmed_at": (
                    order.updated_at or datetime.now(timezone.utc)
                ).isoformat(),
            }
        )
        order.history = history[-_HISTORY_KEEP:]
        order.version = order.version + 1
    order.status = "confirmed"
    order.beat_check = None
    db.commit()
    return _order_out(order)


@router.post("/{chapter_number}/order/unconfirm")
def unconfirm_order(
    chapter_number: int,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤回确认:回到 draft,内容保留;之后的生成回落蓝图行。"""
    order = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
        )
        .first()
    )
    if order is None:
        return {"order": None}
    order.status = "draft"
    db.commit()
    return _order_out(order)


@router.delete("/{chapter_number}/order")
def delete_order(
    chapter_number: int,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除订单:彻底回到蓝图行生成(存量行为)。"""
    deleted = (
        db.query(ChapterOrder)
        .filter(
            ChapterOrder.project_id == project_id,
            ChapterOrder.chapter_number == chapter_number,
        )
        .delete()
    )
    db.commit()
    return {"deleted": deleted}
