"""provider_configs 加 is_default_review 列(review 审校档)

审校档:主审评分/一致性门禁/定点修复(Task.CONSISTENCY)专用配置,写手与
审校分模型,治「同模型自审自写」的评分死锁。未设置时回落 quality 档,
存量用户行为不变。Boolean 列带 server_default='0'(SQLite ADD COLUMN
NOT NULL 不带默认值会炸存量库)。

Revision ID: b3e9f2c41d70
Revises: a7c31f8b52d4
Create Date: 2026-08-29 18:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e9f2c41d70'
down_revision: Union[str, None] = 'a7c31f8b52d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_default_review', sa.Boolean(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.drop_column('is_default_review')
