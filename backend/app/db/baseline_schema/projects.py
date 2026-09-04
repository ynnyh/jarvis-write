# -*- coding: utf-8 -*-
# 基线迁移(0001)的建表/删表 DDL,从 alembic/versions/0001 逐字搬运,按业务域分文件。
# 修改纪律:这里只服务已定版的基线 schema——新表/新列一律写新迁移文件,不改这里;
# 真要动这里,必须先拍 schema 快照证明升级/降级产物与重构前逐字节一致。
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def create_projects() -> None:
    op.create_table('projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('genre', sa.String(length=100), nullable=False),
    sa.Column('target_chapters', sa.Integer(), nullable=False),
    sa.Column('target_words_per_chapter', sa.Integer(), nullable=False),
    sa.Column('word_guard_enabled', sa.Boolean(), nullable=False),
    sa.Column('word_guard_ratio', sa.Float(), nullable=False),
    sa.Column('auto_split_enabled', sa.Boolean(), nullable=False),
    sa.Column('review_pass_threshold', sa.Integer(), nullable=False),
    sa.Column('review_auto_revise', sa.Boolean(), nullable=False),
    sa.Column('review_max_revisions', sa.Integer(), nullable=False),
    sa.Column('queue_require_approved', sa.Boolean(), nullable=False),
    sa.Column('global_tendency', sa.JSON(), nullable=False),
    sa.Column('concept', sa.JSON(), nullable=True),
    sa.Column('dna', sa.JSON(), nullable=True),
    sa.Column('canon', sa.JSON(), nullable=True),
    sa.Column('synopsis', sa.Text(), nullable=True),
    sa.Column('setup_state', sa.String(length=20), nullable=True),
    sa.Column('chat_log', sa.JSON(), nullable=True),
    sa.Column('macro_plan', sa.JSON(), nullable=True),
    sa.Column('style_memo', sa.Text(), nullable=True),
    sa.Column('world_rules', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('outline_stale', sa.Boolean(), nullable=False),
    sa.Column('render_mode', sa.String(length=10), server_default='lite', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_projects_user_id'), ['user_id'], unique=False)


def drop_projects() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_projects_user_id'))

    op.drop_table('projects')


def create_architecture() -> None:
    op.create_table('architecture',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('core_seed', sa.Text(), nullable=False),
    sa.Column('character_dynamics', sa.Text(), nullable=False),
    sa.Column('world_building', sa.Text(), nullable=False),
    sa.Column('plot_architecture', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('concept_stale', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('architecture', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_architecture_project_id'), ['project_id'], unique=False)


def drop_architecture() -> None:
    with op.batch_alter_table('architecture', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_architecture_project_id'))

    op.drop_table('architecture')


def create_outlines() -> None:
    op.create_table('outlines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('chapter_number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('chapter_role', sa.String(length=100), nullable=False),
    sa.Column('chapter_purpose', sa.Text(), nullable=False),
    sa.Column('suspense_level', sa.String(length=50), nullable=False),
    sa.Column('foreshadowing', sa.Text(), nullable=False),
    sa.Column('plot_twist_level', sa.String(length=50), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('beats', sa.JSON(), nullable=False),
    sa.Column('characters_involved', sa.JSON(), nullable=False),
    sa.Column('key_items', sa.JSON(), nullable=False),
    sa.Column('scene_location', sa.String(length=200), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outlines', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outlines_chapter_number'), ['chapter_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_outlines_project_id'), ['project_id'], unique=False)


def drop_outlines() -> None:
    with op.batch_alter_table('outlines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outlines_project_id'))
        batch_op.drop_index(batch_op.f('ix_outlines_chapter_number'))

    op.drop_table('outlines')


def create_outline_versions() -> None:
    op.create_table('outline_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('outline_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('change_type', sa.String(length=10), nullable=False),
    sa.Column('change_summary', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['outline_id'], ['outlines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outline_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outline_versions_outline_id'), ['outline_id'], unique=False)


def drop_outline_versions() -> None:
    with op.batch_alter_table('outline_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outline_versions_outline_id'))

    op.drop_table('outline_versions')


def create_entities() -> None:
    op.create_table('entities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=False),
    sa.Column('base_profile', sa.JSON(), nullable=False),
    sa.Column('retired', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entities_entity_type'), ['entity_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_entities_project_id'), ['project_id'], unique=False)


def drop_entities() -> None:
    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entities_project_id'))
        batch_op.drop_index(batch_op.f('ix_entities_entity_type'))

    op.drop_table('entities')


def create_relationships() -> None:
    op.create_table('relationships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('from_entity_id', sa.Integer(), nullable=False),
    sa.Column('to_entity_id', sa.Integer(), nullable=False),
    sa.Column('relation', sa.String(length=100), nullable=False),
    sa.Column('valid_from', sa.Integer(), nullable=False),
    sa.Column('valid_until', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['from_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_relationships_project_id'), ['project_id'], unique=False)


def drop_relationships() -> None:
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_relationships_project_id'))

    op.drop_table('relationships')


def create_facts() -> None:
    op.create_table('facts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('fact_type', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('valid_from', sa.Integer(), nullable=False),
    sa.Column('valid_until', sa.Integer(), nullable=True),
    sa.Column('importance', sa.String(length=10), nullable=False),
    sa.Column('source_chapter', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('facts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_facts_entity_id'), ['entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_facts_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_facts_valid_from'), ['valid_from'], unique=False)


def drop_facts() -> None:
    with op.batch_alter_table('facts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_facts_valid_from'))
        batch_op.drop_index(batch_op.f('ix_facts_project_id'))
        batch_op.drop_index(batch_op.f('ix_facts_entity_id'))

    op.drop_table('facts')


def create_foreshadowings() -> None:
    op.create_table('foreshadowings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('chapter_planted', sa.Integer(), nullable=False),
    sa.Column('expected_payoff_chapter', sa.Integer(), nullable=True),
    sa.Column('earliest_payoff_chapter', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('payoff_chapter', sa.Integer(), nullable=True),
    sa.Column('reinforcement_chapters', sa.JSON(), nullable=False),
    sa.Column('importance', sa.String(length=10), nullable=False),
    sa.Column('required_hints', sa.JSON(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('foreshadowings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_foreshadowings_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_foreshadowings_status'), ['status'], unique=False)


def drop_foreshadowings() -> None:
    with op.batch_alter_table('foreshadowings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_foreshadowings_status'))
        batch_op.drop_index(batch_op.f('ix_foreshadowings_project_id'))

    op.drop_table('foreshadowings')


def create_writing_cards() -> None:
    op.create_table('writing_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('writing_cards', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_cards_project_id'), ['project_id'], unique=False)


def drop_writing_cards() -> None:
    with op.batch_alter_table('writing_cards', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_cards_project_id'))

    op.drop_table('writing_cards')


def create_knowledge_states() -> None:
    op.create_table('knowledge_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('fact_id', sa.Integer(), nullable=False),
    sa.Column('knower', sa.String(length=50), nullable=False),
    sa.Column('known_from_chapter', sa.Integer(), nullable=False),
    sa.Column('knower_state', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['fact_id'], ['facts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_states', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_states_fact_id'), ['fact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_states_knower'), ['knower'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_states_project_id'), ['project_id'], unique=False)

    # ### end Alembic commands ###


def drop_knowledge_states() -> None:
    with op.batch_alter_table('knowledge_states', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_states_project_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_states_knower'))
        batch_op.drop_index(batch_op.f('ix_knowledge_states_fact_id'))

    op.drop_table('knowledge_states')


