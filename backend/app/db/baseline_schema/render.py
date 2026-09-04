# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_provider_configs() -> None:
    op.create_table('provider_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('interface_format', sa.String(length=20), nullable=False),
    sa.Column('api_key', sa.String(length=300), nullable=False),
    sa.Column('base_url', sa.String(length=300), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('timeout', sa.Integer(), nullable=False),
    sa.Column('max_tokens', sa.Integer(), nullable=False),
    sa.Column('thinking_mode', sa.String(length=10), server_default='', nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('is_default_fast', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_provider_configs_interface_format'), ['interface_format'], unique=False)
        batch_op.create_index(batch_op.f('ix_provider_configs_user_id'), ['user_id'], unique=False)


def drop_provider_configs() -> None:
    with op.batch_alter_table('provider_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_configs_user_id'))
        batch_op.drop_index(batch_op.f('ix_provider_configs_interface_format'))

    op.drop_table('provider_configs')


def create_provider_settings() -> None:
    op.create_table('provider_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('api_key', sa.String(length=300), nullable=False),
    sa.Column('base_url', sa.String(length=300), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'provider', name='uq_provider_per_user')
    )
    with op.batch_alter_table('provider_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_provider_settings_provider'), ['provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_provider_settings_user_id'), ['user_id'], unique=False)


def drop_provider_settings() -> None:
    with op.batch_alter_table('provider_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_settings_user_id'))
        batch_op.drop_index(batch_op.f('ix_provider_settings_provider'))

    op.drop_table('provider_settings')


def create_render_configs() -> None:
    op.create_table('render_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('base_url', sa.String(length=300), nullable=False),
    sa.Column('token', sa.String(length=500), nullable=False),
    sa.Column('resolution', sa.String(length=10), nullable=False),
    sa.Column('workflow_i2v', sa.String(length=120), nullable=False),
    sa.Column('workflow_t2v', sa.String(length=120), nullable=False),
    sa.Column('workflow_tts', sa.String(length=120), nullable=False),
    sa.Column('workflow_talk', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', name='uq_render_config_per_user')
    )
    with op.batch_alter_table('render_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_render_configs_user_id'), ['user_id'], unique=False)


def create_render_tasks() -> None:
    op.create_table('render_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('line', sa.String(length=10), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('episode_id', sa.Integer(), nullable=True),
    sa.Column('shot_id', sa.Integer(), nullable=True),
    sa.Column('clip_id', sa.Integer(), nullable=True),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('workflow_id', sa.String(length=120), nullable=False),
    sa.Column('provider_task_id', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('result_path', sa.String(length=300), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('render_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_render_tasks_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_line'), ['line'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_episode_id'), ['episode_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_shot_id'), ['shot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_clip_id'), ['clip_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_render_tasks_status'), ['status'], unique=False)


def create_tts_tracks() -> None:
    op.create_table('tts_tracks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('cache_key', sa.String(length=16), nullable=False),
    sa.Column('voice_src', sa.String(length=300), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('emotion', sa.String(length=20), nullable=False),
    sa.Column('workflow_id', sa.String(length=120), nullable=False),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('path', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cache_key')
    )
    with op.batch_alter_table('tts_tracks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tts_tracks_user_id'), ['user_id'], unique=False)


def drop_tts_tracks() -> None:
    with op.batch_alter_table('tts_tracks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tts_tracks_user_id'))

    op.drop_table('tts_tracks')


