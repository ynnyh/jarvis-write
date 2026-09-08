"""writing_cards project_fk ondelete CASCADE

P1-6(外键约束开启)配套:writing_cards.project_id 是唯一没声明 ondelete 的
外键,补齐 CASCADE。SQLite 不支持 ALTER 外键,用 batch_alter_table 整表重建;
copy_from 直接取模型当前定义(已含 CASCADE),重建即目标形态,无需按名找约束
(SQLite 的外键在 schema 里是无名的,drop_constraint 按名匹配必失败)。

Revision ID: f6a1c3d9e2b7
Revises: b5c9d2e74f31
Create Date: 2026-09-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6a1c3d9e2b7'
down_revision: Union[str, None] = 'b5c9d2e74f31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists() -> bool:
    from sqlalchemy import inspect

    return "writing_cards" in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists():
        # legacy 库 stamp 跳过基线 DDL,此表可能尚未建:交给 create_all 兜底,
        # 按新模型建自带 CASCADE,无需(也无法)重建
        return

    from app.db.base import Base
    import app.db.models  # noqa: F401 — 确保表定义已注册

    target = Base.metadata.tables["writing_cards"]
    # recreate="always":无操作时 batch 默认跳过重建,外键就不会被重写
    with op.batch_alter_table(
        "writing_cards", copy_from=target, recreate="always",
    ) as batch_op:
        pass  # copy_from 即目标形态:重建即带上 ondelete=CASCADE

    # 重建丢索引(batch 只搬列与约束),project_id 查询走的就是它,补回
    op.create_index(
        "ix_writing_cards_project_id", "writing_cards", ["project_id"]
    )


def downgrade() -> None:
    if not _table_exists():
        return
    # 回滚 = 回到旧的无 ondelete 外键:手写建表定义太重,SQLite 下放弃精确还原,
    # 重建为不带 CASCADE 的同构表(列/索引以 upgrade 时的模型为准,FK 语义退回旧样)
    from sqlalchemy import (
        Boolean, Column, ForeignKey, Integer, MetaData, String, Table, Text,
    )

    meta = MetaData()
    Table(
        "writing_cards", meta,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer,
               ForeignKey("projects.id"), nullable=False, index=True),
        Column("title", String(100), nullable=False),
        Column("body", Text, nullable=False),
        Column("enabled", Boolean, nullable=False),
        Column("sort", Integer, nullable=False),
        Column("created_at", String(40)),
        Column("updated_at", String(40)),
    )
    with op.batch_alter_table("writing_cards", copy_from=meta.tables["writing_cards"]) as batch_op:
        pass
