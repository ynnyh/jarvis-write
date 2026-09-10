"""projects 加 scene_level_enabled 列(场景级生成开关)。

True = 逐场生成 + 逐场验收 + 不合格只重写该场,最后拼成整章;
False(默认)= 一次调用写整章的老路径。存量项目一律 0,行为零变化。

Revision ID: d7b2e5c18f43
Revises: a3f8c1d94e27
Create Date: 2026-09-10 16:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7b2e5c18f43'
down_revision: Union[str, None] = 'a3f8c1d94e27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects') as batch:
        batch.add_column(
            sa.Column(
                'scene_level_enabled',
                sa.Boolean(),
                nullable=False,
                server_default='0',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('projects') as batch:
        batch.drop_column('scene_level_enabled')
