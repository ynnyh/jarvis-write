"""chapter_marks 表(跨章持久化批注:作者在正文里随手记的「这里不行」)

与内存版批注同身份:chapter_number + para_idx + snapshot(原文快照,失效判定用)
+ note(一句话意见)。攒够后由「全书批修」一句总描述驱动逐标记锁情节改写。
见 app/engines/marks.py 与 app/api/marks.py。

Revision ID: e7a3b5c82d91
Revises: c4d8e1a97f25
Create Date: 2026-09-03 13:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3b5c82d91'
down_revision: Union[str, None] = 'c4d8e1a97f25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('chapter_marks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('chapter_number', sa.Integer(), nullable=False),
    sa.Column('para_idx', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter_marks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_marks_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chapter_marks_chapter_number'), ['chapter_number'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('chapter_marks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_marks_chapter_number'))
        batch_op.drop_index(batch_op.f('ix_chapter_marks_project_id'))
    op.drop_table('chapter_marks')
