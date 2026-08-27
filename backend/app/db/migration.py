# app/db/migration.py
# -*- coding: utf-8 -*-
"""Alembic 迁移运行时管理。

处理两种场景:
1. 全新数据库 → 直接 alembic upgrade head(基线迁移建所有表)
2. 现有数据库(pre-Alembic 用户) → 先 stamp 到基线版本,再 upgrade head

为什么需要 stamp:
现有用户的库是 create_all + 手写 migrate.py 建的,没有 alembic_version 表。
直接 upgrade head 会尝试重新建表(表已存在 → 报错)。所以首次运行时:
- 检测到 alembic_version 表不存在
- 且已有业务表(说明是现有用户)
→ 先 stamp 到基线版本(标记基线已应用,不实际执行 DDL)
→ 再正常 upgrade head(应用基线之后的新迁移)

之后的启动:alembic_version 表已存在,直接 upgrade head 即可。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from app.paths import resource_path

from sqlalchemy import inspect, text

from app.db.session import engine

logger = logging.getLogger("jarvis-write.migration")

# 基线迁移的 revision ID(与 alembic/versions/0001_baseline_initial_schema.py 一致)
BASELINE_REVISION = "fe553853d66d"

# 用于判断"已有业务表"的标志表(项目核心表,一定存在)
_SIGNATURE_TABLE = "projects"


def _alembic_dir() -> Path:
    """迁移脚本目录(冻结/源码两态一致),供程序化注入 script_location。

    不依赖 alembic.ini 里写的 %(here)s 插值——那条只有在 ini 恰好被完整
    打包且解析成功时才成立,一旦 ini 缺键/损坏/被打漏,Config 解析到
    script_location 为空就会抛「No 'script_location' key found in configuration」,
    迁移整个静默跳过。这里用 resource_path 统一定位：
    - 源码        → backend/alembic
    - 冻结(有打包)→ _MEIPASS/alembic
    - 冻结(漏打包)→ _MEIPASS/alembic(维护在 _alembic_ini_path 里的显式告警)
    """
    return resource_path("alembic")


def _alembic_ini_path() -> str:
    """解析 alembic.ini 的绝对路径。

    源码环境:backend/alembic.ini
    冻结环境(PyInstaller):_MEIPASS/alembic.ini
    """
    logger = logging.getLogger("jarvis-write.migration")
    # 优先用环境变量(测试或特殊部署可覆盖)
    env_path = os.environ.get("ALEMBIC_CONFIG")
    if env_path and Path(env_path).exists():
        return env_path

    # 冻结环境:_MEIPASS 是 PyInstaller 解包目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "alembic.ini"
        if candidate.exists():
            return str(candidate)
        # 缺包子产:不再静默回退到"当前目录/根下碰运气",显式告警。
        # 历史教训:桌面安装包漏打 alembic.ini+alembic(旧 onedir 构建),
        # 迁移这边每次启动都静默失败、回退 create_all —— 升级老库时
        # 只有 Alembic 迁移负责的新列永远建不出来,API 查询 500,界面表现为
        # 「进不到首页」。把问题亮出来,避免下次又无声无息。
        if (Path(meipass) / "alembic").exists():
            logger.warning("迁移:打包找不到 alembic.ini,将程序化注入默认 script_location")
            return str(Path(meipass) / "alembic.ini")
        logger.error(
            "迁移:打包产物缺失 alembic.ini 与 alembic/ 迁移脚本"
            "(旧构建或 spec 漏打数据文件)。Alembic 迁移不会生效,"
            "仅靠 create_all+legacy migrate 兜底;升级老库时 Alembic 专属的新列可能缺失。"
            "请重新按 scripts/build-desktop.sh 构建安装包。"
        )
        return ""

    # 源码环境:相对 backend/ 目录
    # app/db/migration.py → backend/app/db/migration.py → backend/
    backend_dir = Path(__file__).resolve().parent.parent.parent
    candidate = backend_dir / "alembic.ini"
    if candidate.exists():
        return str(candidate)

    # 兜底:当前工作目录
    return "alembic.ini"


def _has_alembic_version_table() -> bool:
    """检查数据库中是否已有 alembic_version 表。"""
    insp = inspect(engine)
    return "alembic_version" in insp.get_table_names()


def _has_business_tables() -> bool:
    """检查数据库中是否已有业务表(说明是 pre-Alembic 的现有用户)。"""
    insp = inspect(engine)
    return _SIGNATURE_TABLE in insp.get_table_names()


def _stamp_to_baseline() -> None:
    """把数据库标记为已应用基线迁移(不实际执行 DDL)。

    用于现有用户数据库:表已经由 create_all + migrate.py 建好了,
    只需要在 alembic_version 表里插入基线版本号,让 Alembic 知道
    "基线已经应用过了,从下一个迁移开始跑"。
    """
    from alembic.config import Config
    from alembic import command

    ini_path = _alembic_ini_path()
    logger.info("首次集成 Alembic:现有数据库 stamp 到基线版本 %s", BASELINE_REVISION)
    logger.info("alembic.ini 路径: %s", ini_path)

    cfg = Config(ini_path) if ini_path else Config()
    # 程序化注入 script_location:不再依赖 ini 的 %(here)s 插值/键值。
    # 脚本目录经 resource_path 解析(冻结=_MEIPASS/alembic,源码=backend/alembic),
    # 与 desktop.spec 的 datas 落点一致;ini 缺键/损坏/被打漏也不会再报
    # 「No 'script_location' key found in configuration」。
    cfg.set_main_option("script_location", str(_alembic_dir()))
    # 程序化调用必须关掉 alembic 的日志接管:ini 里的 [loggers] 段会触发
    # fileConfig(disable_existing_loggers=True),把应用已建好的全部 logger
    # 一键禁用,之后所有业务日志静默消失。[loggers] 段保留给开发者 CLI 用。
    cfg.attributes["configure_logger"] = False
    # stamp 只写 alembic_version 表,不执行任何 DDL
    command.stamp(cfg, BASELINE_REVISION)
    logger.info("stamp 完成,数据库已标记为基线版本")


def _upgrade_head() -> None:
    """运行 alembic upgrade head,应用所有待执行的迁移。"""
    from alembic.config import Config
    from alembic import command

    ini_path = _alembic_ini_path()
    logger.info("运行 Alembic 迁移: upgrade head")
    logger.info("alembic.ini 路径: %s", ini_path)

    cfg = Config(ini_path) if ini_path else Config()
    # 同 _stamp_to_baseline:程序化注入 script_location,摆脱对 ini 的依赖
    cfg.set_main_option("script_location", str(_alembic_dir()))
    # 同上:不许 alembic 接管应用日志(见 _stamp_to_baseline 内注释)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    logger.info("Alembic 迁移完成")


def run_alembic_migrations() -> None:
    """启动时调用:运行 Alembic 数据库迁移。

    流程:
    1. 没有 alembic_version 表 + 有业务表 → 现有用户,先 stamp 基线
    2. 没有 alembic_version 表 + 没有业务表 → 全新安装,直接 upgrade
    3. 有 alembic_version 表 → 正常 upgrade
    """
    try:
        if not _has_alembic_version_table():
            if _has_business_tables():
                # 现有用户数据库:表已存在,先 stamp 到基线
                _stamp_to_baseline()
            # else: 全新数据库,直接 upgrade 即可
        _upgrade_head()
    except Exception:
        # Alembic 失败时不阻断启动:create_all 会作为兜底建表,
        # migrate.py 的幂等补列也会跑。记录错误让用户排查。
        logger.exception("Alembic 迁移失败,将回退到 create_all + legacy migrate", exc_info=True)
