"""baseline initial schema

Revision ID: fe553853d66d
Revises: 
Create Date: 2026-08-26 17:50:51.839947

建表/删表 DDL 按业务域拆在 app/db/baseline_schema/(2026-09 重构):
本文件只保留 revision 标识与执行顺序,DDL 逐字搬运、顺序原样——
升级/降级产物与拆分前逐字节一致(schema 快照对比验证)。
新表/新列写新迁移文件,不改这里。
"""
from __future__ import annotations

from typing import Sequence, Union

from app.db.baseline_schema import DOWNGRADE_STEPS, UPGRADE_STEPS


# revision identifiers, used by Alembic.
revision: str = 'fe553853d66d'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for step in UPGRADE_STEPS:
        step()


def downgrade() -> None:
    for step in DOWNGRADE_STEPS:
        step()
