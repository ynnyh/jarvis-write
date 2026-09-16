"""文风画像(docs/20 同批):projects.style_profile 结构化六维画像。

一份真相:可视化画像卡、续集分析产物、手改都写回这一列;注入时渲染进 style_block。
原生 ALTER ADD COLUMN(FTS 触发器纪律)。

Revision ID: f30c3d5e7a92
Revises: e20b2c4d6f81
Create Date: 2026-09-16 14:05:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f30c3d5e7a92'
down_revision: Union[str, None] = 'e20b2c4d6f81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'projects' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('projects')}
        if 'style_profile' not in cols:
            op.execute("ALTER TABLE projects ADD COLUMN style_profile JSON")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'projects' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('projects')}
        if 'style_profile' in cols:
            op.execute("ALTER TABLE projects DROP COLUMN style_profile")
