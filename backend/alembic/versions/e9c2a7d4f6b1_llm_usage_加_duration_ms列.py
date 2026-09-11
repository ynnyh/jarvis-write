"""llm_usage 加 duration_ms 列(生成质量观测的响应时间维度)。

毫秒级单次调用耗时(含网络+生成);旧记录默认 0(=没量到,聚合按缺席处理)。
老库 stamp 路径可能没有 llm_usage 表(基线后才有):不存在则跳过,
由启动时的 create_all 按新模型建表——迁移不许假设表存在(test_db_migration 纪律)。

Revision ID: e9c2a7d4f6b1
Revises: b7e4f2a91c58
Create Date: 2026-09-11 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9c2a7d4f6b1'
down_revision: Union[str, None] = 'b7e4f2a91c58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'llm_usage' not in insp.get_table_names():
        return
    with op.batch_alter_table('llm_usage') as batch:
        batch.add_column(
            sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'llm_usage' not in insp.get_table_names():
        return
    with op.batch_alter_table('llm_usage') as batch:
        batch.drop_column('duration_ms')
