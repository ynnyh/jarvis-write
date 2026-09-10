"""事实消费日志表:fact_usages(§1.5 引用追踪)。

让「一条事实作废 → 哪些章要复核」从全文扫描降为一次索引查询。一行 =
一个 (fact, 消费章) 对;source 区分可信度(retrieval / extract / manual)。

Revision ID: b7e4f2a91c58
Revises: d7b2e5c18f43
Create Date: 2026-09-10 17:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4f2a91c58'
down_revision: Union[str, None] = 'd7b2e5c18f43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'fact_usages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('fact_id', sa.Integer(), nullable=False),
        sa.Column('chapter_number', sa.Integer(), nullable=False),
        sa.Column('scene_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=12), nullable=False,
                  server_default='retrieval'),
        sa.Column('times', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('evidence', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['fact_id'], ['facts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fact_usages_project_id', 'fact_usages', ['project_id'])
    op.create_index('ix_fact_usages_fact_id', 'fact_usages', ['fact_id'])
    op.create_index('ix_fact_usages_chapter_number', 'fact_usages',
                    ['chapter_number'])
    op.create_index('ix_fact_usages_fact_chapter', 'fact_usages',
                    ['fact_id', 'chapter_number'])
    op.create_index('ix_fact_usages_project_chapter', 'fact_usages',
                    ['project_id', 'chapter_number'])


def downgrade() -> None:
    op.drop_index('ix_fact_usages_project_chapter', table_name='fact_usages')
    op.drop_index('ix_fact_usages_fact_chapter', table_name='fact_usages')
    op.drop_index('ix_fact_usages_chapter_number', table_name='fact_usages')
    op.drop_index('ix_fact_usages_fact_id', table_name='fact_usages')
    op.drop_index('ix_fact_usages_project_id', table_name='fact_usages')
    op.drop_table('fact_usages')
