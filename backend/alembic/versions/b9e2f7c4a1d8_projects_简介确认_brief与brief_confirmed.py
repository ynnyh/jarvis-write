"""确认链 L0:projects.brief + projects.brief_confirmed

开书对话式确认流(2026-09-24):选流派/没灵感不再直接抽卡——先和策划聊出
一版完整简介(brief),作者拍板(brief_confirmed)才解锁概念深化(后端 409 把关)。
每出新草稿自动回未拍板(重新上锁);存量书两列为默认值,行为零变化。

Revision ID: b9e2f7c4a1d8
Revises: c7d4e8f2a1b9
Create Date: 2026-09-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'b9e2f7c4a1d8'
down_revision: Union[str, None] = 'c7d4e8f2a1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table}).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    conn = op.get_bind()
    # legacy 库可能没有 projects 表:交给 create_all 按新模型建表,自带新列
    if _table_exists(conn, "projects") and not _column_exists(conn, "projects", "brief"):
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN brief TEXT DEFAULT '' NOT NULL"
        ))
    if _table_exists(conn, "projects") and not _column_exists(conn, "projects", "brief_confirmed"):
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN brief_confirmed BOOLEAN DEFAULT 0 NOT NULL"
        ))


def downgrade() -> None:
    # SQLite 老 version 不支持 DROP COLUMN;两列均为增量语义,降级留空即可
    pass
