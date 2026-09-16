"""开续集(docs/20 同批):projects.sequel_of_id 续集来源列。

不加 FK 约束:前作删除不应连带续集。原生 ALTER ADD COLUMN(与 FTS 触发器
纪律一致;projects 表无触发器,但统一走原生 ALTER)。

Revision ID: e20b2c4d6f81
Revises: d20a1b3c5e7f
Create Date: 2026-09-16 13:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e20b2c4d6f81'
down_revision: Union[str, None] = 'd20a1b3c5e7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'projects' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('projects')}
        if 'sequel_of_id' not in cols:
            op.execute("ALTER TABLE projects ADD COLUMN sequel_of_id INTEGER")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'projects' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('projects')}
        if 'sequel_of_id' in cols:
            op.execute("ALTER TABLE projects DROP COLUMN sequel_of_id")
