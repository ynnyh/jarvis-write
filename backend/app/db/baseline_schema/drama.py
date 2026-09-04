# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_drama_episodes() -> None:
    op.create_table('drama_episodes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('ep_index', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('source_chapter', sa.Integer(), nullable=False),
    sa.Column('source_chapters', sa.JSON(), nullable=False),
    sa.Column('hook', sa.Text(), nullable=False),
    sa.Column('recap', sa.Text(), nullable=False),
    sa.Column('cliffhanger', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('duration_target_s', sa.Integer(), nullable=False),
    sa.Column('script', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'ep_index', name='uq_drama_ep_index')
    )
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_episodes_project_id'), ['project_id'], unique=False)


def drop_drama_episodes() -> None:
    with op.batch_alter_table('drama_episodes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_episodes_project_id'))

    op.drop_table('drama_episodes')


def create_drama_style_cards() -> None:
    op.create_table('drama_style_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('style_name', sa.String(length=60), nullable=False),
    sa.Column('direction', sa.String(length=40), nullable=False),
    sa.Column('style_cn', sa.Text(), nullable=False),
    sa.Column('style_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('ratio', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('drama_style_cards', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_style_cards_project_id'), ['project_id'], unique=True)


def drop_drama_style_cards() -> None:
    with op.batch_alter_table('drama_style_cards', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_style_cards_project_id'))

    op.drop_table('drama_style_cards')


def create_drama_scene_cards() -> None:
    op.create_table('drama_scene_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('appearance_cn', sa.Text(), nullable=False),
    sa.Column('appearance_en', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_drama_scene_name')
    )
    with op.batch_alter_table('drama_scene_cards', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_scene_cards_project_id'), ['project_id'], unique=False)


def drop_drama_scene_cards() -> None:
    with op.batch_alter_table('drama_scene_cards', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_scene_cards_project_id'))

    op.drop_table('drama_scene_cards')


def create_drama_character_cards() -> None:
    op.create_table('drama_character_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('gender', sa.String(length=10), nullable=False),
    sa.Column('appearance_cn', sa.Text(), nullable=False),
    sa.Column('appearance_en', sa.Text(), nullable=False),
    sa.Column('outfit_cn', sa.Text(), nullable=False),
    sa.Column('voice_desc', sa.Text(), nullable=False),
    sa.Column('tts_hint', sa.Text(), nullable=False),
    sa.Column('reading_notes', sa.Text(), nullable=False),
    sa.Column('ref_prompt_cn', sa.Text(), nullable=False),
    sa.Column('ref_prompt_en', sa.Text(), nullable=False),
    sa.Column('ref_images', sa.JSON(), nullable=False),
    sa.Column('voice_ref', sa.String(length=300), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_drama_char_name')
    )
    with op.batch_alter_table('drama_character_cards', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_character_cards_project_id'), ['project_id'], unique=False)


def drop_drama_character_cards() -> None:
    with op.batch_alter_table('drama_character_cards', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_character_cards_project_id'))

    op.drop_table('drama_character_cards')


def create_drama_trailers() -> None:
    op.create_table('drama_trailers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('target_s', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('lines', sa.JSON(), nullable=False),
    sa.Column('shots', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('drama_trailers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_trailers_project_id'), ['project_id'], unique=True)


def drop_drama_trailers() -> None:
    with op.batch_alter_table('drama_trailers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_trailers_project_id'))

    op.drop_table('drama_trailers')


def create_drama_shots() -> None:
    op.create_table('drama_shots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('episode_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('scene_name', sa.String(length=200), nullable=False),
    sa.Column('characters', sa.JSON(), nullable=False),
    sa.Column('action_desc', sa.Text(), nullable=False),
    sa.Column('shot_type', sa.String(length=20), nullable=False),
    sa.Column('camera', sa.String(length=20), nullable=False),
    sa.Column('dialogue', sa.Text(), nullable=False),
    sa.Column('emotion', sa.String(length=20), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=False),
    sa.Column('prompt_cn', sa.Text(), nullable=False),
    sa.Column('prompt_en', sa.Text(), nullable=False),
    sa.Column('negative', sa.Text(), nullable=False),
    sa.Column('motion_cn', sa.Text(), nullable=False),
    sa.Column('motion_en', sa.Text(), nullable=False),
    sa.Column('assets', sa.JSON(), nullable=False),
    sa.Column('clip_ref', sa.String(length=500), nullable=False),
    sa.Column('done_still', sa.Boolean(), nullable=False),
    sa.Column('done_video', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['drama_episodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('drama_shots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_shots_episode_id'), ['episode_id'], unique=False)


def drop_drama_shots() -> None:
    with op.batch_alter_table('drama_shots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_shots_episode_id'))

    op.drop_table('drama_shots')


def create_drama_production_packs() -> None:
    op.create_table('drama_production_packs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('episode_id', sa.Integer(), nullable=False),
    sa.Column('pack', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['drama_episodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('drama_production_packs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drama_production_packs_episode_id'), ['episode_id'], unique=True)


def drop_drama_production_packs() -> None:
    with op.batch_alter_table('drama_production_packs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drama_production_packs_episode_id'))

    op.drop_table('drama_production_packs')


