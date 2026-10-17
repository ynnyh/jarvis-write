"""前置重构:projects 补理解卡/骨架失配/软删三列 + gen_inputs 留痕表(docs/32 §2.5)

- projects.intent_card(JSON):开书确认过的理解卡(世界坐标/视角主角/味道/必须/不要/题材),
  全链生成的最高约束。NULL = 老项目,下游回落旧行为。
- projects.skeleton_stale(BOOL NOT NULL DEFAULT 0):架构改动后骨架仍挂在旧架构上
  (补上「架构→骨架」这一跳,与既有 outline_stale 同模式)。
- projects.deleted_at(DATETIME):软删留痕(docs/29)。置位即前台不可见,超期才物理清理。
- gen_inputs:每次生成「吃了哪些用户输入」(三问答案/引导语/带话/理解卡草稿)的只追加快照——
  复盘「为什么偏」时最关键的证据此前全在客户端内存里,刷新即失。

Revision ID: d4a1f7c9e2b8
Revises: e8f2b6c9a4d1
Create Date: 2026-10-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'd4a1f7c9e2b8'
down_revision: Union[str, None] = 'b7e2f4a6c8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table}).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    conn = op.get_bind()
    # 新表直接按模型元数据建:DDL 与模型逐字对齐,漂移门禁零噪音(同 skill_packs 的做法)
    if not _table_exists(conn, "gen_inputs"):
        from app.db.base import Base
        import app.db.models  # noqa: F401 — 注册全部模型

        Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables["gen_inputs"]])
    # legacy 库可能没有 projects 表:交给 create_all 按新模型建表,自带新列
    if _table_exists(conn, "projects"):
        if not _column_exists(conn, "projects", "intent_card"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN intent_card JSON"))
        if not _column_exists(conn, "projects", "skeleton_stale"):
            conn.execute(text(
                "ALTER TABLE projects ADD COLUMN skeleton_stale BOOLEAN DEFAULT 0 NOT NULL"
            ))
        if not _column_exists(conn, "projects", "deleted_at"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN deleted_at DATETIME"))


def downgrade() -> None:
    # SQLite 老版本不支持 DROP COLUMN;三列与新表均为增量语义,降级留空即可
    pass
