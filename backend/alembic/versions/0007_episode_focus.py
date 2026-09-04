"""drama_episodes 加 focus 列(每集改编意图,写剧本时高优先级注入)

「本集重点」是作者对这一集的再创作指令(如「重点拍那场对峙」),随集落库,
重写剧本不丢。Text 列带 server_default=''(SQLite ADD COLUMN NOT NULL
不带默认值会炸存量库)。

Revision ID: f2b9d4c61e08
Revises: e7a3b5c82d91
Create Date: 2026-09-04 09:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b9d4c61e08'
down_revision: Union[str, None] = 'e7a3b5c82d91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('focus', sa.Text(), nullable=False, server_default='')
        )


def downgrade() -> None:
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.drop_column('focus')
