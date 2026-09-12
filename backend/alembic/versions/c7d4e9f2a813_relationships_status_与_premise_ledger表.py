"""交稿对账(docs/19 M3):relationships.status 确认流 + premise_ledger 表。

抽取写的新关系边 = pending(不注入、不进作战图,等作者在交稿对账确认/否决);
存量边迁移为 confirmed(它们一直在驱动生成,翻 pending 会静默撤掉安全网)。
premise_ledger 记每章梗兑现账(同章唯一,重抽取覆盖),交稿对账与梗健康度共用。

Revision ID: c7d4e9f2a813
Revises: b3e7a1c65f42
Create Date: 2026-09-12 19:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d4e9f2a813'
down_revision: Union[str, None] = 'b3e7a1c65f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'relationships' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('relationships')}
        if 'status' not in cols:
            with op.batch_alter_table('relationships') as batch:
                batch.add_column(
                    sa.Column('status', sa.String(length=12), nullable=False,
                              server_default='confirmed')
                )
            op.create_index('ix_relationships_status', 'relationships', ['status'])
        # 存量边一直都在驱动生成:保持 confirmed,不翻 pending
    if 'premise_ledger' not in insp.get_table_names():
        op.create_table(
            'premise_ledger',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(),
                      sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
            sa.Column('chapter_number', sa.Integer(), nullable=False),
            sa.Column('fulfilled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('beat', sa.String(length=100), nullable=False, server_default=''),
            sa.Column('note', sa.Text(), nullable=False, server_default=''),
            sa.Column('strength', sa.Integer(), nullable=False, server_default='3'),
            sa.Column('evidence', sa.Text(), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('project_id', 'chapter_number'),
        )
        op.create_index('ix_premise_ledger_project_id', 'premise_ledger', ['project_id'])
        op.create_index('ix_premise_ledger_chapter_number', 'premise_ledger', ['chapter_number'])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'premise_ledger' in insp.get_table_names():
        op.drop_index('ix_premise_ledger_chapter_number', table_name='premise_ledger')
        op.drop_index('ix_premise_ledger_project_id', table_name='premise_ledger')
        op.drop_table('premise_ledger')
    if 'relationships' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('relationships')}
        if 'status' in cols:
            idx = {i['name'] for i in insp.get_indexes('relationships')}
            if 'ix_relationships_status' in idx:
                op.drop_index('ix_relationships_status', table_name='relationships')
            with op.batch_alter_table('relationships') as batch:
                batch.drop_column('status')
