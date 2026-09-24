"""docs/21 创作Skill包:skill_packs 表(官方包 seed + 按节点注入 + 版本回退)

- skill_packs:成套工艺包实体(scope 适用线 / entries 条目集 / version+history
  版本化)。首批 seed 两个动漫线试点包(分镜功底包、镜头卡渲染工艺包),
  seed 逻辑在 engines/skills/packs.py,启动后首次使用时幂等补种。

Revision ID: c7d4e8f2a1b9
Revises: e5f6a7b8c9d0
Create Date: 2026-09-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'c7d4e8f2a1b9'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table}).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "skill_packs"):
        # 直接按模型元数据建表:DDL 与模型逐字对齐,漂移门禁零噪音
        from app.db.base import Base
        import app.db.models  # noqa: F401 — 注册全部模型

        Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables["skill_packs"]])


def downgrade() -> None:
    pass
