# app/paths.py
# -*- coding: utf-8 -*-
"""资源与数据目录解析:兼容源码运行与 PyInstaller 冻结(桌面版)。

- resource_path():只读资源(static / frontend dist)。冻结后随 exe 解包到
  sys._MEIPASS 临时目录,源码运行时相对仓库定位。
- user_data_dir():可写数据目录(SQLite / 日志)。桌面版落用户目录
  (Windows: %APPDATA%\\jarvis-write),卸载/升级不丢作品。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(rel: str) -> Path:
    """定位打包进来的只读资源。

    冻结环境:sys._MEIPASS 是 PyInstaller 解包的临时根目录,资源按 spec
    的 datas 相对路径放在其下,统一以此为基准。

    源码环境:资源分布在两个根——backend/(如 app/static)与仓库根
    (如 frontend/dist)。先在 backend/ 找,不存在再回落仓库根,让
    `resource_path("app/static")` 与 `resource_path("frontend/dist")`
    都能命中,无需调用方分支。
    """
    if is_frozen():
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        return base / rel
    backend_dir = Path(__file__).resolve().parents[1]   # backend/
    p = backend_dir / rel
    if p.exists():
        return p
    repo_root = backend_dir.parent                       # 仓库根
    return repo_root / rel


def user_data_dir() -> Path:
    """可写数据目录。

    桌面(local)版:Windows %APPDATA%\\jarvis-write,其他系统 ~/.jarvis-write。
    可用环境变量 JARVIS_DATA_DIR 覆盖。目录不存在则创建。
    """
    override = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if override:
        d = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / "jarvis-write"
    else:
        d = Path.home() / ".jarvis-write"
    d.mkdir(parents=True, exist_ok=True)
    return d
