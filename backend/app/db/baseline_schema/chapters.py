# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_chapter_summaries() -> None:
    op.create_table('chapter_summaries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('chapter_number', sa.Integer(), nullable=False),
    sa.Column('rolling_summary', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter_summaries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_summaries_chapter_number'), ['chapter_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_chapter_summaries_project_id'), ['project_id'], unique=False)


def drop_chapter_summaries() -> None:
    with op.batch_alter_table('chapter_summaries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_summaries_project_id'))
        batch_op.drop_index(batch_op.f('ix_chapter_summaries_chapter_number'))

    op.drop_table('chapter_summaries')


def create_chapters() -> None:
    op.create_table('chapters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('outline_id', sa.Integer(), nullable=True),
    sa.Column('chapter_number', sa.Integer(), nullable=False),
    sa.Column('draft_content', sa.Text(), nullable=False),
    sa.Column('final_content', sa.Text(), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('outline_version_used', sa.Integer(), nullable=False),
    sa.Column('is_stale', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('review_snapshot', sa.Text(), nullable=False),
    sa.Column('proofread_snapshot', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['outline_id'], ['outlines.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapters_chapter_number'), ['chapter_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_chapters_project_id'), ['project_id'], unique=False)


def drop_chapters() -> None:
    with op.batch_alter_table('chapters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapters_project_id'))
        batch_op.drop_index(batch_op.f('ix_chapters_chapter_number'))

    op.drop_table('chapters')


def create_chapter_issues() -> None:
    op.create_table('chapter_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chapter_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('issue_type', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.Column('suggestion', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('content_hash', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter_issues', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_issues_chapter_id'), ['chapter_id'], unique=False)


def drop_chapter_issues() -> None:
    with op.batch_alter_table('chapter_issues', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_issues_chapter_id'))

    op.drop_table('chapter_issues')


def create_chapter_states() -> None:
    op.create_table('chapter_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chapter_id', sa.Integer(), nullable=False),
    sa.Column('contract', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=16), nullable=False),
    sa.Column('extract_status', sa.String(length=20), nullable=False),
    sa.Column('extract_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter_states', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_states_chapter_id'), ['chapter_id'], unique=True)


def drop_chapter_states() -> None:
    with op.batch_alter_table('chapter_states', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_states_chapter_id'))

    op.drop_table('chapter_states')


def create_chapter_versions() -> None:
    op.create_table('chapter_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chapter_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('draft_content', sa.Text(), nullable=False),
    sa.Column('final_content', sa.Text(), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_versions_chapter_id'), ['chapter_id'], unique=False)


def drop_chapter_versions() -> None:
    with op.batch_alter_table('chapter_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_versions_chapter_id'))

    op.drop_table('chapter_versions')


