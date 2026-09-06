"""分享链接表:整本书/单章公开只读链接(免登录阅读,可撤销)。

Revision ID: e7f2a9c84b30
Revises: d8c3e5a72b19
Create Date: 2026-09-06 19:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f2a9c84b30'
down_revision: Union[str, None] = 'd8c3e5a72b19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'share_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False, server_default='book'),
        sa.Column('chapter_number', sa.Integer(), nullable=True),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('view_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_share_links_token', 'share_links', ['token'], unique=True)
    op.create_index('ix_share_links_project_id', 'share_links', ['project_id'])
    op.create_index('ix_share_links_user_id', 'share_links', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_share_links_user_id', table_name='share_links')
    op.drop_index('ix_share_links_project_id', table_name='share_links')
    op.drop_index('ix_share_links_token', table_name='share_links')
    op.drop_table('share_links')
