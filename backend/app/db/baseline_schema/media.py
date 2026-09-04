# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_birthday_wishes() -> None:
    op.create_table('birthday_wishes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('occasion', sa.String(length=20), server_default='birthday', nullable=False),
    sa.Column('honoree_name', sa.String(length=60), nullable=False),
    sa.Column('relationship', sa.String(length=20), nullable=False),
    sa.Column('milestone', sa.String(length=80), nullable=False),
    sa.Column('memories', sa.JSON(), nullable=False),
    sa.Column('sender_desc', sa.String(length=80), nullable=False),
    sa.Column('tone', sa.String(length=40), nullable=False),
    sa.Column('custom_tone', sa.String(length=120), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('pack', sa.String(length=40), server_default='', nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('style_hints', sa.String(length=160), server_default='', nullable=False),
    sa.Column('style_name', sa.String(length=60), nullable=False),
    sa.Column('style_cn', sa.Text(), nullable=False),
    sa.Column('style_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('candidates', sa.JSON(), nullable=False),
    sa.Column('chosen', sa.Integer(), nullable=False),
    sa.Column('clip', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('birthday_wishes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_birthday_wishes_user_id'), ['user_id'], unique=False)


def drop_birthday_wishes() -> None:
    with op.batch_alter_table('birthday_wishes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_birthday_wishes_user_id'))

    op.drop_table('birthday_wishes')


def create_birthday_shoots() -> None:
    op.create_table('birthday_shoots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('wish_id', sa.Integer(), nullable=False),
    sa.Column('shoot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['wish_id'], ['birthday_wishes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('birthday_shoots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_birthday_shoots_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_birthday_shoots_wish_id'), ['wish_id'], unique=True)


def drop_birthday_shoots() -> None:
    with op.batch_alter_table('birthday_shoots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_birthday_shoots_wish_id'))
        batch_op.drop_index(batch_op.f('ix_birthday_shoots_user_id'))

    op.drop_table('birthday_shoots')


def create_mood_clips() -> None:
    op.create_table('mood_clips',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('source_project_id', sa.Integer(), nullable=True),
    sa.Column('mode', sa.String(length=20), server_default='mood', nullable=False),
    sa.Column('theme', sa.String(length=40), nullable=False),
    sa.Column('custom_theme', sa.String(length=120), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('inspiration', sa.Text(), nullable=False),
    sa.Column('dialogue_style', sa.String(length=20), server_default='auto', nullable=False),
    sa.Column('pacing', sa.String(length=20), server_default='auto', nullable=False),
    sa.Column('intensity', sa.String(length=20), server_default='auto', nullable=False),
    sa.Column('style_hints', sa.String(length=160), server_default='', nullable=False),
    sa.Column('style_name', sa.String(length=60), nullable=False),
    sa.Column('style_cn', sa.Text(), nullable=False),
    sa.Column('style_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('candidates', sa.JSON(), nullable=False),
    sa.Column('chosen', sa.Integer(), nullable=False),
    sa.Column('clip', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['source_project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mood_clips', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mood_clips_source_project_id'), ['source_project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mood_clips_user_id'), ['user_id'], unique=False)


def drop_mood_clips() -> None:
    with op.batch_alter_table('mood_clips', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mood_clips_user_id'))
        batch_op.drop_index(batch_op.f('ix_mood_clips_source_project_id'))

    op.drop_table('mood_clips')


def create_promo_plans() -> None:
    op.create_table('promo_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('subject', sa.String(length=120), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('angles', sa.JSON(), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('style_name', sa.String(length=60), nullable=False),
    sa.Column('style_cn', sa.Text(), nullable=False),
    sa.Column('style_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('landmarks', sa.JSON(), nullable=False),
    sa.Column('material_notes', sa.Text(), nullable=False),
    sa.Column('chat_log', sa.JSON(), nullable=False),
    sa.Column('brief', sa.JSON(), nullable=False),
    sa.Column('brief_locked', sa.Boolean(), nullable=False),
    sa.Column('script', sa.JSON(), nullable=False),
    sa.Column('pack', sa.JSON(), nullable=False),
    sa.Column('chunks', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('promo_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promo_plans_user_id'), ['user_id'], unique=False)


def drop_promo_plans() -> None:
    with op.batch_alter_table('promo_plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promo_plans_user_id'))

    op.drop_table('promo_plans')


def create_promo_shots() -> None:
    op.create_table('promo_shots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('promo_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('scene_name', sa.String(length=200), nullable=False),
    sa.Column('characters', sa.JSON(), nullable=False),
    sa.Column('action_desc', sa.Text(), nullable=False),
    sa.Column('shot_type', sa.String(length=20), nullable=False),
    sa.Column('camera', sa.String(length=20), nullable=False),
    sa.Column('dialogue', sa.Text(), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('prompt_cn', sa.Text(), nullable=False),
    sa.Column('prompt_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['promo_id'], ['promo_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('promo_shots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promo_shots_promo_id'), ['promo_id'], unique=False)


def drop_promo_shots() -> None:
    with op.batch_alter_table('promo_shots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promo_shots_promo_id'))

    op.drop_table('promo_shots')


def create_clip_shoots() -> None:
    op.create_table('clip_shoots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('clip_id', sa.Integer(), nullable=False),
    sa.Column('shoot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['clip_id'], ['mood_clips.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('clip_shoots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clip_shoots_clip_id'), ['clip_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_clip_shoots_user_id'), ['user_id'], unique=False)


def drop_clip_shoots() -> None:
    with op.batch_alter_table('clip_shoots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clip_shoots_user_id'))
        batch_op.drop_index(batch_op.f('ix_clip_shoots_clip_id'))

    op.drop_table('clip_shoots')


def create_series_characters() -> None:
    op.create_table('series_characters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('look', sa.Text(), nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('default_duration_s', sa.Integer(), nullable=False),
    sa.Column('style_hints', sa.String(length=160), server_default='', nullable=False),
    sa.Column('ref_images', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('series_characters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_series_characters_user_id'), ['user_id'], unique=False)


def create_series_episodes() -> None:
    op.create_table('series_episodes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('character_id', sa.Integer(), nullable=False),
    sa.Column('plot', sa.Text(), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('output', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['character_id'], ['series_characters.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('series_episodes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_series_episodes_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_series_episodes_character_id'), ['character_id'], unique=False)


