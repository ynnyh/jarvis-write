"""relationships 加 evidence_fact_id 证据锚(docs/19 M2 追溯层)。

抽取写边时指向同章双写的 fact 行(source_chapter + 完整关系描述),
边→证据直达,替代 consistency.py 里 other_name 字符串反查的脆弱组装;
存量边该列为 NULL,证据组装回退到原字符串匹配,行为向后兼容。

Revision ID: b3e7a1c65f42
Revises: a5f8c2d94e17
Create Date: 2026-09-12 18:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7a1c65f42'
down_revision: Union[str, None] = 'a5f8c2d94e17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'relationships' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('relationships')}
    if 'evidence_fact_id' not in cols:
        with op.batch_alter_table('relationships') as batch:
            batch.add_column(sa.Column('evidence_fact_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'relationships' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('relationships')}
    if 'evidence_fact_id' in cols:
        with op.batch_alter_table('relationships') as batch:
            batch.drop_column('evidence_fact_id')
