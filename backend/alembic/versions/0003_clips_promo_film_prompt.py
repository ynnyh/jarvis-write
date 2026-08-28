"""mood_clips / promo_plans 补 film_prompt 列(整片提示词)

TEXT NOT NULL 必须带 server_default——SQLite 的 ADD COLUMN NOT NULL 不带默认值
直接报错,存量库 upgrade 会当场炸;'' 与模型侧 default="" 同语义。
覆盖三处工坊:mood_clips(情绪短片/灵感工坊/故事工坊)+ promo_plans(宣传片)。

Revision ID: a7c31f8b52d4
Revises: d144572e9db9
Create Date: 2026-08-28 21:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c31f8b52d4'
down_revision: Union[str, None] = 'd144572e9db9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('mood_clips', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('film_prompt', sa.Text(), nullable=False, server_default='')
        )
    with op.batch_alter_table('promo_plans', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('film_prompt', sa.Text(), nullable=False, server_default='')
        )


def downgrade() -> None:
    with op.batch_alter_table('promo_plans', schema=None) as batch_op:
        batch_op.drop_column('film_prompt')
    with op.batch_alter_table('mood_clips', schema=None) as batch_op:
        batch_op.drop_column('film_prompt')
