"""章节反馈表:chapter_feedback(用户对生成结果的归因闭环,docs/17 M2)。

一行 = 一个用户对一章的表态(可改判,upsert);差评带四桶分类,
与截断率/降级信号交叉归因。表不存在才建;存在则跳过(幂等)。

Revision ID: c3d8e1f5a9b2
Revises: e9c2a7d4f6b1
Create Date: 2026-09-11 12:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d8e1f5a9b2'
down_revision: Union[str, None] = 'e9c2a7d4f6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'chapter_feedback' in insp.get_table_names():
        return  # 幂等:老路径 create_all 已建过
    op.create_table(
        'chapter_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chapter_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.String(length=8), nullable=False),
        sa.Column('categories', sa.JSON(), nullable=True),
        sa.Column('comment', sa.Text(), nullable=False, server_default=''),
        sa.Column('content_hash', sa.String(length=16), nullable=False,
                  server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chapter_id', 'user_id',
                            name='uq_feedback_chapter_user'),
    )
    op.create_index('ix_chapter_feedback_chapter_id', 'chapter_feedback',
                    ['chapter_id'])
    op.create_index('ix_chapter_feedback_user_id', 'chapter_feedback',
                    ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_chapter_feedback_user_id', table_name='chapter_feedback')
    op.drop_index('ix_chapter_feedback_chapter_id', table_name='chapter_feedback')
    op.drop_table('chapter_feedback')
