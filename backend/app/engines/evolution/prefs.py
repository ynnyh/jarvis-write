# app/engines/evolution/prefs.py
# -*- coding: utf-8 -*-
"""用户级自治旋钮(docs/36 §2):off / confirm / auto,缺行即 off。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import EvolutionPref

LEVELS: tuple[str, ...] = ("off", "confirm", "auto")
DEFAULT_LEVEL = "off"


def get_user_level(db: Session, user_id: int | None) -> str:
    """读用户档位;无行/非法值一律回落 off(坏值不能放大权限)。"""
    if user_id is None:
        return DEFAULT_LEVEL
    row = db.get(EvolutionPref, user_id)
    if row is None or row.level not in LEVELS:
        return DEFAULT_LEVEL
    return row.level


def set_user_level(db: Session, user_id: int, level: str) -> None:
    row = db.get(EvolutionPref, user_id)
    if row is None:
        row = EvolutionPref(user_id=user_id, level=level)
        db.add(row)
    else:
        row.level = level
