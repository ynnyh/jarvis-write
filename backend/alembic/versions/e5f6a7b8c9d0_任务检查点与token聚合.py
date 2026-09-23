"""Phase 4.2:job_steps 检查点表 + llm_usage.job_id 归属列

- job_steps:长任务昂贵子步骤的落库留痕(clips 每卡/场景级每场),
  排查「死在哪一步」、成本归因、断点续跑的数据底座
- llm_usage.job_id:调用级 token 账归属到任务,聚合出「这一章花了多少」

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
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
    if not _table_exists(conn, "job_steps"):
        # 直接按模型元数据建表:DDL 与模型逐字对齐,漂移门禁零噪音
        from app.db.base import Base
        import app.db.models  # noqa: F401 — 注册全部模型

        Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables["job_steps"]])
    if _table_exists(conn, "llm_usage") and not _column_exists(conn, "llm_usage", "job_id"):
        conn.execute(text("ALTER TABLE llm_usage ADD COLUMN job_id VARCHAR(12)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_job_id ON llm_usage (job_id)"
        ))


def downgrade() -> None:
    pass
