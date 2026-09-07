"""剧本工坊表:scripts(剧本)+ script_episodes(集,每集剧本正文)。

独立创作与小说改编共用;改编时 source_project_id 指向源小说。

Revision ID: b5c9d2e74f31
Revises: e7f2a9c84b30
Create Date: 2026-09-07 08:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c9d2e74f31'
down_revision: Union[str, None] = 'e7f2a9c84b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scripts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_project_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('genre', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('logline', sa.Text(), nullable=False, server_default=''),
        sa.Column('target_episodes', sa.Integer(), nullable=False, server_default='12'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='empty'),
        sa.Column('style_memo', sa.Text(), nullable=False, server_default=''),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_project_id'], ['projects.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scripts_user_id', 'scripts', ['user_id'])

    op.create_table(
        'script_episodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('script_id', sa.Integer(), nullable=False),
        sa.Column('episode_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('synopsis', sa.Text(), nullable=False, server_default=''),
        sa.Column('opening_hook', sa.Text(), nullable=False, server_default=''),
        sa.Column('ending_hook', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='empty'),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('word_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['script_id'], ['scripts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_script_episodes_script_id', 'script_episodes', ['script_id'])


def downgrade() -> None:
    op.drop_index('ix_script_episodes_script_id', table_name='script_episodes')
    op.drop_table('script_episodes')
    op.drop_index('ix_scripts_user_id', table_name='scripts')
    op.drop_table('scripts')
