"""writing_motifs 表(桥段台账 + 雷区清单,跨章重复描写治理)

一张表两种行:台账行(auto,每 (project, chapter, label) 一行,章后抽取/全书
扫描写入)+ 雷区行(user,(project, label) 唯一,作者明令禁止的桥段,全书
生效)。用途与聚合口径见 app/engines/consistency/motifs.py。

Revision ID: c4d8e1a97f25
Revises: a52a7aed8264
Create Date: 2026-09-03 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8e1a97f25'
down_revision: Union[str, None] = 'a52a7aed8264'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('writing_motifs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('chapter_number', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('banned', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('writing_motifs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_motifs_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('writing_motifs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_motifs_project_id'))
    op.drop_table('writing_motifs')
