# scripts/check_schema_drift.py
# -*- coding: utf-8 -*-
"""Schema 漂移门禁:SQLAlchemy 模型与 Alembic 迁移链必须严格一致。

引入 Alembic 后,改表的标准动作是「改 models → alembic revision --autogenerate
生成迁移」。漏做第二步时,本地开发看不出任何异常(create_all 兜底会把缺的列
建出来),而存量用户的库走 upgrade head 永远补不上——这类事故上线才炸。

本脚本对"升级到 head 的临时空库"跑一次 autogenerate 比对:模型与迁移链完全
一致时 diff 为空;有任何差异(缺表/缺列/类型不符)即非零退出。CI 的后端
job 在 pytest 之后跑这一步。

本地手动检查:
    cd backend && python scripts/check_schema_drift.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# 必须在导入 app.config / alembic 之前钉住:env.py 运行时会用
# settings.database_url 覆盖 Config 里的 sqlalchemy.url,不设环境变量的话
# upgrade 会打到默认库(backend/jarvis_write.db,即真实数据)!
_tmp_db = Path(tempfile.mktemp(suffix="_drift.db"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.as_posix()}"

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from sqlalchemy import Column, Index, Table, create_engine  # noqa: E402

import app.db.models  # noqa: F401,E402  注册全部模型到 metadata
from app.db.base import Base  # noqa: E402
from app.db.migration import _alembic_ini_path  # noqa: E402


def _on_fts_shadow(item: tuple) -> bool:
    """漂移项是否落在 fts_* 上:0008 建的 FTS5 虚表(连同 SQLite 自动衍生的
    _content/_data/_idx/_config/_docsize 影子表)刻意不进 ORM,触发器负责同步。
    与 tests/test_db_migration.py 的排除是同一份纪律,两处必须一起改。"""
    for el in item[1:]:
        if isinstance(el, Table) and el.name.startswith("fts_"):
            return True
        if isinstance(el, (Column, Index)) and el.table is not None and el.table.name.startswith("fts_"):
            return True
    return False


def main() -> int:
    url = os.environ["DATABASE_URL"]

    cfg = Config(_alembic_ini_path())
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False  # 不接管应用日志(见 migration.py 同款注释)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
        _tmp_db.unlink(missing_ok=True)

    diff = [d for d in diff if not _on_fts_shadow(d)]

    if not diff:
        print("schema 无漂移:模型与 Alembic 迁移链一致。")
        return 0

    print(f"发现 {len(diff)} 处 schema 漂移——改了 models 但没生成迁移,存量用户将补不上这些变更:")
    for item in diff:
        print(f"  - {item}")
    print("修复:cd backend && alembic revision --autogenerate -m \"描述\",核对后随代码一起提交。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
