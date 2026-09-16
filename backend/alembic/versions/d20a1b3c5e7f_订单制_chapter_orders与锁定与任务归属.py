"""订单制(docs/20):chapter_orders 表 + outlines.locked + jobs 归属三列。

章节订单(写前确认单)新表;章纲作者锁(级联/批量重铺短路);
任务归属解析列(任务中心按书分组,旧任务 NULL 回退平铺)。

Revision ID: d20a1b3c5e7f
Revises: c7d4e9f2a813
Create Date: 2026-09-16 12:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd20a1b3c5e7f'
down_revision: Union[str, None] = 'c7d4e9f2a813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(insp, table: str) -> set:
    return {c['name'] for c in insp.get_columns(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    # ---- chapter_orders 新表 ----
    if 'chapter_orders' not in insp.get_table_names():
        op.create_table(
            'chapter_orders',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(), nullable=False, index=True),
            sa.Column('chapter_number', sa.Integer(), nullable=False, index=True),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=10), nullable=False, server_default='draft'),
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('history', sa.JSON(), nullable=False),
            sa.Column('beat_check', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('project_id', 'chapter_number', name='uq_order_chapter'),
        )
        op.create_index('ix_chapter_orders_status', 'chapter_orders', ['status'])

    # ---- outlines.locked ----
    # 注意:必须用原生 ALTER TABLE ADD COLUMN,不能用 batch_alter_table——
    # batch 模式「建新表→拷数据→改名」会把 0008 建的 FTS 同步触发器一并丢掉,
    # 此后大纲改动不再进 fts_outlines,全书检索的大纲组静默变空(实测复现过)。
    if 'outlines' in insp.get_table_names():
        cols = _cols(insp, 'outlines')
        if 'locked' not in cols:
            op.execute("ALTER TABLE outlines ADD COLUMN locked BOOLEAN NOT NULL DEFAULT 0")

    # ---- jobs 归属三列(同上,原生 ALTER,不动表结构本体) ----
    if 'jobs' in insp.get_table_names():
        cols = _cols(insp, 'jobs')
        if 'project_id' not in cols:
            op.execute("ALTER TABLE jobs ADD COLUMN project_id INTEGER")
        if 'chapter_number' not in cols:
            op.execute("ALTER TABLE jobs ADD COLUMN chapter_number INTEGER")
        if 'parent_job_id' not in cols:
            op.execute("ALTER TABLE jobs ADD COLUMN parent_job_id VARCHAR(12)")
        if 'project_id' not in cols:
            op.create_index('ix_jobs_project_id', 'jobs', ['project_id'])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'jobs' in insp.get_table_names():
        cols = _cols(insp, 'jobs')
        if 'project_id' in cols:
            op.drop_index('ix_jobs_project_id', table_name='jobs')
        for col in ('parent_job_id', 'chapter_number', 'project_id'):
            if col in cols:
                op.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
    if 'outlines' in insp.get_table_names() and 'locked' in _cols(insp, 'outlines'):
        op.execute("ALTER TABLE outlines DROP COLUMN locked")
    if 'chapter_orders' in insp.get_table_names():
        op.drop_table('chapter_orders')
