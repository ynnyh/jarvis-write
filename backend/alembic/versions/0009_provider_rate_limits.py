"""provider_configs 加并发/RPM 主动限速两列(0 = 不限,存量行为不变)

限速按「渠道 + 模型」维度生效(见 app/llm/throttle.py):同一中转站同一模型的
上游配额是共享的,多用户站上防 429/防封号、防止一人把共享配额打爆。
Integer 列带 server_default='0':SQLite ADD COLUMN NOT NULL 必须给默认。

Revision ID: d8c3e5a72b19
Revises: c9f4a2d73b15
Create Date: 2026-09-05 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8c3e5a72b19'
down_revision: Union[str, None] = 'c9f4a2d73b15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_concurrency', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('rpm', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.drop_column('rpm')
        batch_op.drop_column('max_concurrency')
