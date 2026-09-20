"""动画短剧对话式简介:anime_episodes 加 chat / synopsis / synopsis_ok 三列。

用户点子先和 AI 多轮聊(chat 线程),AI 补充完善出本集简介(synopsis),
用户确认(synopsis_ok=1)后才解锁分镜;选定梗纲的捷径路径也写简介并标记确认。

Revision ID: c5f1a8d3e7b2
Revises: b3e9f4a6c2d8
Create Date: 2026-09-20 14:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f1a8d3e7b2'
down_revision: Union[str, None] = 'b3e9f4a6c2d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('anime_episodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chat', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('synopsis', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('synopsis_ok', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('anime_episodes', schema=None) as batch_op:
        batch_op.drop_column('synopsis_ok')
        batch_op.drop_column('synopsis')
        batch_op.drop_column('chat')
