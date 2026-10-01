"""为动画系列增加独立工作区标识。"""
from alembic import op
import sqlalchemy as sa

revision = "b7e2f4a6c8d0"
down_revision = "a6f1c2d3e4b5"
branch_labels = None
depends_on = None


def upgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("anime_series")}
    if "workspace" not in cols:
        op.execute("ALTER TABLE anime_series ADD COLUMN workspace VARCHAR(30) NOT NULL DEFAULT 'anime'")
        op.create_index("ix_anime_series_workspace", "anime_series", ["workspace"])


def downgrade():
    op.drop_index("ix_anime_series_workspace", table_name="anime_series")
    op.drop_column("anime_series", "workspace")
