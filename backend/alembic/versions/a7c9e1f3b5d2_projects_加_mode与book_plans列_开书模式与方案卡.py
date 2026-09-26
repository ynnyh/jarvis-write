"""projects 加 mode + book_plans 列:开书模式分叉与整书方案卡墙

docs/22 P0:开书入口前置「开哪种书」(mode: serial=开书连载/short=短故事),
简介对谈升级为「三问定纲 → 整书方案×3」。mode 落库供方案生成与后续 P1 轻管线
分叉;book_plans 存方案卡工作集(含定向修订版),拍板时渲染成开书订单写入 brief。
存量项目 mode='serial'(server_default),行为零变化。

Revision ID: a7c9e1f3b5d2
Revises: b9e2f7c4a1d8
Create Date: 2026-09-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c9e1f3b5d2"
down_revision: Union[str, None] = "b9e2f7c4a1d8"
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
        if "mode" not in cols:
            batch_op.add_column(
                sa.Column("mode", sa.String(10), nullable=False, server_default="serial")
            )
        if "book_plans" not in cols:
            batch_op.add_column(sa.Column("book_plans", sa.JSON(), nullable=True))


def downgrade() -> None:
    cols = _columns()
    if not cols:
        return
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        if "book_plans" in cols:
            batch_op.drop_column("book_plans")
        if "mode" in cols:
            batch_op.drop_column("mode")
