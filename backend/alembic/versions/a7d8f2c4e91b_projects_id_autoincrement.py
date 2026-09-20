"""开书串档修复:projects.id 改 AUTOINCREMENT,删书重建不再复用 id。

复用链路(用户实测踩中):删掉 id 最大的书 → 新建草稿拿到同一个 id →
前端 localStorage 按 pid 存的向导缓存(提示文字/候选卡/引擎卡)被当成
新书的草稿原样灌回——「删书重开后提示文字和卡片还是上次的」。
AUTOINCREMENT 让 id 只增不复用;老库需整表重建(SQLite 无法原地加),
重建逻辑与启动兜底共用 app.migrate.rebuild_projects_autoincrement。

Revision ID: a7d8f2c4e91b
Revises: f30c3d5e7a92
Create Date: 2026-09-20 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'a7d8f2c4e91b'
down_revision: Union[str, None] = 'f30c3d5e7a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    from app.migrate import rebuild_projects_autoincrement

    # PRAGMA foreign_keys 在事务内改是静默 no-op,整表重建须在 autocommit 块里做
    with op.get_context().autocommit_block():
        rebuild_projects_autoincrement(op.get_bind())


def downgrade() -> None:
    # 单向改造:恢复「id 可复用」没有业务价值,不做反向重建
    pass
