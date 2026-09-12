"""核心梗卡表 + outlines 补 premise_beat 列(docs/19 M1)。

梗卡是一本书的「纲」:高概念/兑现机制/节拍表/边界禁忌/钩子计划,蓝图逐章
标「梗兑现」以此为准。premises 是新表(启动时 create_all 也会建,迁移幂等);
outlines.premise_beat 老库补列,空串=未标,行为向后兼容。

Revision ID: a5f8c2d94e17
Revises: c3d8e1f5a9b2
Create Date: 2026-09-12 18:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5f8c2d94e17'
down_revision: Union[str, None] = 'c3d8e1f5a9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'premises' not in insp.get_table_names():
        op.create_table(
            'premises',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(),
                      sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
            sa.Column('kind', sa.String(length=10), nullable=False, server_default='main'),
            sa.Column('high_concept', sa.Text(), nullable=False, server_default=''),
            sa.Column('payoff', sa.Text(), nullable=False, server_default=''),
            sa.Column('beats', sa.JSON(), nullable=False),
            sa.Column('boundaries', sa.JSON(), nullable=False),
            sa.Column('hook_plan', sa.JSON(), nullable=False),
            sa.Column('source', sa.String(length=10), nullable=False, server_default='ai'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('project_id'),
        )
        op.create_index('ix_premises_project_id', 'premises', ['project_id'], unique=True)
    if 'outlines' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('outlines')}
        if 'premise_beat' not in cols:
            with op.batch_alter_table('outlines') as batch:
                batch.add_column(sa.Column('premise_beat', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'premises' in insp.get_table_names():
        op.drop_index('ix_premises_project_id', table_name='premises')
        op.drop_table('premises')
    if 'outlines' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('outlines')}
        if 'premise_beat' in cols:
            with op.batch_alter_table('outlines') as batch:
                batch.drop_column('premise_beat')
