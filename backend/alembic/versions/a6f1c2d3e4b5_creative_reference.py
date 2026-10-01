"""参考驱动创作与完整单集剧本。"""
from alembic import op
import sqlalchemy as sa

revision = "a6f1c2d3e4b5"
down_revision = "e8f2b6c9a4d1"
branch_labels = None
depends_on = None

ADDITIONS = {
    "anime_series": {"creative_goal": "JSON"},
    "anime_episodes": {"script": "JSON", "creative_stale": "BOOLEAN NOT NULL DEFAULT 0", "guests": "JSON"},
}


def upgrade():
    for table, columns in ADDITIONS.items():
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        for column, ddl in columns.items():
            if column not in cols:
                op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def downgrade():
    for table, columns in ADDITIONS.items():
        for column in reversed(columns):
            op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
