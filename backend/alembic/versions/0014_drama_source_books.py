"""漫剧源书(docs/23):projects 加 audience / mounted_packs 两列。

drama 开书模式的频道标记与书级 skill 包挂载清单;mode 列本身不加不动
(serial/short 存量语义不变,新增值 drama 由应用层写入,迁移无需触碰)。

Revision ID: e8f2b6c9a4d1
Revises: a7c9e1f3b5d2
Create Date: 2026-09-26 15:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8f2b6c9a4d1"
down_revision: Union[str, None] = "a7c9e1f3b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("audience", sa.String(10), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("mounted_packs", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("mounted_packs")
        batch_op.drop_column("audience")
