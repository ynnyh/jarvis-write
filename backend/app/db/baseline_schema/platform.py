# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_app_settings() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('value', sa.String(length=500), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def drop_app_settings() -> None:
    op.drop_table('app_settings')


def create_invite_codes() -> None:
    op.create_table('invite_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('max_uses', sa.Integer(), nullable=True),
    sa.Column('used_count', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invite_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invite_codes_code'), ['code'], unique=True)


def drop_invite_codes() -> None:
    with op.batch_alter_table('invite_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invite_codes_code'))

    op.drop_table('invite_codes')


def create_jobs() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('stage', sa.String(length=200), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)


def drop_jobs() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_jobs_owner_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_kind'))

    op.drop_table('jobs')


def create_llm_usage() -> None:
    op.create_table('llm_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_usage_model'), ['model'], unique=False)
        batch_op.create_index(batch_op.f('ix_llm_usage_user_id'), ['user_id'], unique=False)


def drop_llm_usage() -> None:
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_usage_user_id'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_model'))

    op.drop_table('llm_usage')


def create_tendency_presets() -> None:
    op.create_table('tendency_presets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('is_builtin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tendency_presets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tendency_presets_scope'), ['scope'], unique=False)


def drop_tendency_presets() -> None:
    with op.batch_alter_table('tendency_presets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tendency_presets_scope'))

    op.drop_table('tendency_presets')


def create_users() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)


def drop_users() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')


def create_feature_usage() -> None:
    op.create_table('feature_usage',
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('uses', sa.Integer(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('feature', 'user_id')
    )


