"""outlines 补情绪基调与本章戏核

蓝图层就定下「这一章是什么调子、必须让读者记住哪一瞬」,正文才谈得上
「该精彩时精彩、该压抑时压抑」——此前每章的情绪全靠写正文时随机,全书
成了一条平线。存量蓝图两列为空串,drama_task 自动回落到让模型自定,行为不变。

注:本文件由 alembic revision --autogenerate 起稿后**手工精简**——自动版把本地
库落后的历史变更(含 FTS 虚表的增删)一并写了进来,那些不属于本次改动。

Revision ID: 2e99af271b27
Revises: c8d4f6a9e1b3
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2e99af271b27'
down_revision: Union[str, None] = 'c8d4f6a9e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'outlines',
        sa.Column('emotional_tone', sa.String(length=100), nullable=False, server_default=''),
    )
    op.add_column(
        'outlines',
        sa.Column('scene_anchor', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('outlines', 'scene_anchor')
    op.drop_column('outlines', 'emotional_tone')
