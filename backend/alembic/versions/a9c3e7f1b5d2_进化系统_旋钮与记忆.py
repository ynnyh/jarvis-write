"""进化系统:evolution_prefs 旋钮表 + evolution_memories 记忆表(docs/36)

- evolution_prefs(user_id PK, level):用户级自治旋钮 off/confirm/auto,缺行即 off。
- evolution_memories:复盘提炼的记忆条目(user_id 隔离,project_id 故意不加 FK——
  项目删了教训还在;kind/status 白名单在引擎层收敛,库里不设枚举约束)。

Revision ID: a9c3e7f1b5d2
Revises: d4a1f7c9e2b8
Create Date: 2026-10-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'a9c3e7f1b5d2'
down_revision: Union[str, None] = 'd4a1f7c9e2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table}).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    # 新表直接按模型元数据建:DDL 与模型逐字对齐,漂移门禁零噪音(同 gen_inputs 的做法)
    missing = [t for t in ("evolution_prefs", "evolution_memories") if not _table_exists(conn, t)]
    if missing:
        from app.db.base import Base
        import app.db.models  # noqa: F401 — 注册全部模型

        Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables[t] for t in missing])


def downgrade() -> None:
    op.drop_table("evolution_memories")
    op.drop_table("evolution_prefs")
