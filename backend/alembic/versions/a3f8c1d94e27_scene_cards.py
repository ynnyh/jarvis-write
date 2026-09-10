"""场景卡表:scenes(生成单元)+ scene_versions(场景版本快照)。

把「章」降级为容器、把「场景」升格为真正的生成单元:每个场景自带情绪指令、
张力强度、目标字数与验收结论,逐场景生成、逐场景验收、不合格只重写该场景。
独立成表(而非 outlines 的 JSON 列)是因为场景要有自己的状态机、版本号、
正文锚点与验收记录。

Revision ID: a3f8c1d94e27
Revises: 2e99af271b27
Create Date: 2026-09-10 16:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f8c1d94e27'
down_revision: Union[str, None] = '2e99af271b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scenes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('outline_id', sa.Integer(), nullable=False),
        sa.Column('chapter_number', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('location', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('characters', sa.JSON(), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False, server_default=''),
        sa.Column('conflict', sa.Text(), nullable=False, server_default=''),
        sa.Column('emotion_target', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('tension_level', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('target_words', sa.Integer(), nullable=False, server_default='1500'),
        sa.Column('fact_hints', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='planned'),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('word_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('anchor_start', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('anchor_end', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('joins_previous', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('accept_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('accept_scores', sa.JSON(), nullable=False),
        sa.Column('rewrite_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['outline_id'], ['outlines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scenes_project_id', 'scenes', ['project_id'])
    op.create_index('ix_scenes_outline_id', 'scenes', ['outline_id'])
    op.create_index('ix_scenes_chapter_number', 'scenes', ['chapter_number'])
    op.create_index('ix_scenes_status', 'scenes', ['status'])

    op.create_table(
        'scene_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scene_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('word_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='generated'),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scene_versions_scene_id', 'scene_versions', ['scene_id'])


def downgrade() -> None:
    op.drop_index('ix_scene_versions_scene_id', table_name='scene_versions')
    op.drop_table('scene_versions')
    op.drop_index('ix_scenes_status', table_name='scenes')
    op.drop_index('ix_scenes_chapter_number', table_name='scenes')
    op.drop_index('ix_scenes_outline_id', table_name='scenes')
    op.drop_index('ix_scenes_project_id', table_name='scenes')
    op.drop_table('scenes')
