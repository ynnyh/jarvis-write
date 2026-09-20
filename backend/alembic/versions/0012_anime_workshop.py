"""动画短剧工坊:建 anime_series / anime_episodes 两张表。

全新库走 Alembic 建表(模型只保证 create_all 路径),新增工坊的两张表
必须进迁移链,否则 test_fresh_db_upgrade / schema_drift 双双报漂移。

Revision ID: b3e9f4a6c2d8
Revises: a7d8f2c4e91b
Create Date: 2026-09-20 12:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e9f4a6c2d8'
down_revision: Union[str, None] = 'a7d8f2c4e91b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'anime_series',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('premise', sa.Text(), nullable=False, server_default=''),
        sa.Column('genre', sa.String(length=40), nullable=False, server_default='comedy'),
        sa.Column('direction', sa.String(length=40), nullable=False, server_default='chibi'),
        sa.Column('style_cn', sa.Text(), nullable=False, server_default=''),
        sa.Column('cast', sa.JSON(), nullable=False),
        sa.Column('episode_s', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='cast_empty'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_anime_series_user_id', 'anime_series', ['user_id'])

    op.create_table(
        'anime_episodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('series_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('title', sa.String(length=60), nullable=False, server_default=''),
        sa.Column('premise', sa.Text(), nullable=False, server_default=''),
        sa.Column('takes', sa.JSON(), nullable=False),
        sa.Column('chosen', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('shots', sa.JSON(), nullable=False),
        sa.Column('film_prompt', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='premise'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['series_id'], ['anime_series.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('series_id', 'seq', name='uq_anime_ep_seq'),
    )
    op.create_index('ix_anime_episodes_user_id', 'anime_episodes', ['user_id'])
    op.create_index('ix_anime_episodes_series_id', 'anime_episodes', ['series_id'])


def downgrade() -> None:
    op.drop_index('ix_anime_episodes_series_id', table_name='anime_episodes')
    op.drop_index('ix_anime_episodes_user_id', table_name='anime_episodes')
    op.drop_table('anime_episodes')
    op.drop_index('ix_anime_series_user_id', table_name='anime_series')
    op.drop_table('anime_series')
