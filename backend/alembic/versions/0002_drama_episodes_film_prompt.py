"""drama_episodes 补 film_prompt 列(整片提示词,幂等由 migrate.py 与本链各管一层)

整片提示词:端到端音频原生视频模型(Sora/Veo/可灵)用的一次性成片提示词。
TEXT NOT NULL 必须带 server_default——SQLite 的 ADD COLUMN NOT NULL 不带默认值
直接报错,存量库 upgrade 会当场炸;'' 与模型侧 default="" 同语义。

Revision ID: d144572e9db9
Revises: fe553853d66d
Create Date: 2026-08-28 20:41:25.662621

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd144572e9db9'
down_revision: Union[str, None] = 'fe553853d66d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('film_prompt', sa.Text(), nullable=False, server_default='')
        )


def downgrade() -> None:
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.drop_column('film_prompt')
