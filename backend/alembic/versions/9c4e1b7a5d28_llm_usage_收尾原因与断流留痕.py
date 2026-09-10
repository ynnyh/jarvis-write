"""llm_usage 增加 finish_reason / truncated 留痕列

背景(2026-09-08 50 章压测):魔芋中转会把响应尾巴随机掐断,连 16 字符的
JSON 都被截在半途。表象是「解析失败」,但根因有两种,对策完全不同:
- finish_reason=length → 输出预算用尽,加 max_tokens 即可;
- 无 [DONE] 也无 finish_reason → 网关/CDN 静默掐断,加预算没用,要换渠道或续写。

此前 finish_reason 只在内存里用于空正文归因,没落库,事后无法回溯某个渠道
到底是哪种。这里补两列,给渠道体检与「要不要换渠道」的判断留证据。

Revision ID: 9c4e1b7a5d28
Revises: f6a1c3d9e2b7
Create Date: 2026-09-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4e1b7a5d28"
down_revision: Union[str, None] = "f6a1c3d9e2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

_TABLE = "llm_usage"


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
        if "finish_reason" not in cols:
            batch_op.add_column(
                sa.Column("finish_reason", sa.String(length=20), nullable=False, server_default="")
            )
        if "truncated" not in cols:
            batch_op.add_column(
                sa.Column("truncated", sa.Boolean(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    cols = _columns()
    if not cols:
        return
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        if "truncated" in cols:
            batch_op.drop_column("truncated")
        if "finish_reason" in cols:
            batch_op.drop_column("finish_reason")
