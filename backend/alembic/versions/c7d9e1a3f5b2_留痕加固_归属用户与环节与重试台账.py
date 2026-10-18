"""留痕加固:gen_inputs 归属用户 + llm_usage 生成环节 + llm_retries 重试台账(docs/37)

- gen_inputs.user_id(INT NULL, index):灵感工坊等无项目上下文的输入按人归档。
- llm_usage.task(VARCHAR(40) DEFAULT ''):生成环节名(Task 枚举值),成本与
  稳定率分环节归因的源头。
- llm_retries(新表):每次渠道瞬时错误重试落一行——此前只进 live 内存,
  重启即失,「渠道稳不稳」没有任何持久账本。

Revision ID: c7d9e1a3f5b2
Revises: a9c3e7f1b5d2
Create Date: 2026-10-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'c7d9e1a3f5b2'
down_revision: Union[str, None] = 'a9c3e7f1b5d2'
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
    if _table_exists(conn, "gen_inputs") and not _column_exists(conn, "gen_inputs", "user_id"):
        conn.execute(text("ALTER TABLE gen_inputs ADD COLUMN user_id INTEGER"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gen_inputs_user_id ON gen_inputs (user_id)"))
    if _table_exists(conn, "llm_usage") and not _column_exists(conn, "llm_usage", "task"):
        conn.execute(text("ALTER TABLE llm_usage ADD COLUMN task VARCHAR(40) NOT NULL DEFAULT ''"))
    # 新表按模型元数据建:DDL 与模型逐字对齐,漂移门禁零噪音
    if not _table_exists(conn, "llm_retries"):
        from app.db.base import Base
        import app.db.models  # noqa: F401 — 注册全部模型

        Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables["llm_retries"]])


def downgrade() -> None:
    op.drop_table("llm_retries")
    # SQLite 旧版不支持 DROP COLUMN,列留在原地无害(与 heal_missing_columns 同宽容口径)
