"""确认链:projects.concept_confirmed + architecture.confirmed_layers

docs/确认链 L1/L2:
- projects.concept_confirmed:概念拍板标记(概念打磨屏「拍板」置 True;概念内容再变自动复位)
- architecture.confirmed_layers:架构逐层拍板 JSON({layer_key: bool});
  NULL = 存量架构(未拆层时代生成),按全层已认解释,旧行为零变化

Revision ID: d1e2f3a4b5c6
Revises: c5f1a8d3e7b2
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c5f1a8d3e7b2'
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
    # legacy 库可能两表皆无:交给 create_all 按新模型建表,自带新列
    if _table_exists(conn, "projects") and not _column_exists(conn, "projects", "concept_confirmed"):
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN concept_confirmed BOOLEAN DEFAULT 0 NOT NULL"
        ))
    if _table_exists(conn, "architecture") and not _column_exists(conn, "architecture", "confirmed_layers"):
        conn.execute(text(
            "ALTER TABLE architecture ADD COLUMN confirmed_layers JSON"
        ))


def downgrade() -> None:
    # SQLite 不支持 DROP COLUMN(老版本);确认链列均为增量语义,降级留空即可
    pass
