"""projects 加 open_ended 列:开放式连载(结局未定)模式

病根:契约式架构开局就要规划第三幕(结局)与全书伏笔闭环,总章数被当成
「体量承诺」钉死;长篇写到铺满 target_chapters 后没有正路续(只有手动改
设置的暗门,卷纲也不跟着长)。真实连载是「写到哪续到哪」——本列给项目
打上连载式标记:架构只定长线引擎与首批方向,蓝图铺满后「展开下一卷」
自动续订体量。False(默认)= 契约式,存量项目行为零变化。

Revision ID: c8d4f6a9e1b3
Revises: 9c4e1b7a5d28
Create Date: 2026-09-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d4f6a9e1b3"
down_revision: Union[str, None] = "9c4e1b7a5d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

_TABLE = "projects"


def _columns() -> set[str]:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    cols = _columns()
    if not cols:
        # legacy 库 stamp 跳过基线 DDL,表可能还没建:交给 create_all 兜底
        return
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        if "open_ended" not in cols:
            batch_op.add_column(
                sa.Column("open_ended", sa.Boolean(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    cols = _columns()
    if not cols:
        return
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        if "open_ended" in cols:
            batch_op.drop_column("open_ended")
