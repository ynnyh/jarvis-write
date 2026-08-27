"""Alembic 环境配置。

与项目现有配置集成:
- 数据库 URL 从 app.config.get_settings() 读取(支持 DATABASE_URL 环境变量)
- 目标 metadata 用 app.db.base.Base.metadata(所有模型已在 app.db.models 注册)
- SQLite 开启 render_as_batch=True,兼容 SQLite 的 ALTER TABLE 限制
- 桌面版(PyInstaller 冻结)时,迁移脚本路径从 _MEIPASS 解析

运行:
  cd backend && alembic upgrade head    # 应用所有迁移
  cd backend && alembic revision --autogenerate -m "描述"  # 生成新迁移
"""
from __future__ import annotations

import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# 确保 backend 目录在 sys.path 中(alembic 从 backend/ 运行时已在,
# 但 PyInstaller 冻结或从其他目录调用时需要显式添加)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.db.models  # noqa: E402,F401  确保所有模型被注册到 metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 注意:这里绝不能 fileConfig(config.config_file_name)——fileConfig 默认
# disable_existing_loggers=True,而迁移是在应用进程内跑的(main.py lifespan
# 先 setup_logging 再跑迁移),这一调会把已建好的全部业务 logger 一键禁用,
# 日志从此静默。日志统一走应用自己的 setup_logging;CLI 单独跑 alembic 时
# 输出走 root logger 的默认行为。

# 从项目配置读取数据库 URL,覆盖 alembic.ini 里的占位值
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 兼容:自动用 batch 模式处理 ALTER
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite 兼容:自动用 batch 模式处理 ALTER
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
