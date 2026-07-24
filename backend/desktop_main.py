# desktop_main.py
# -*- coding: utf-8 -*-
"""桌面版(PyInstaller 冻结)入口:单机 local 模式,免登录,数据落用户目录。

与服务器版的区别全部在启动前用环境变量拍定,app 代码零分叉:
- APP_MODE=local        → 免登录、单用户、放行本机源(见 app/auth.py、main.py)
- DATABASE_URL          → SQLite 落 %APPDATA%\\jarvis-write\\jarvis_write.db
- 只监听 127.0.0.1      → 免鉴权仅在本机可达,不对外暴露(安全前提)

端口默认 8756(避开常见占用),被占用时顺延找空闲口;实际端口打印到 stdout,
供桌面壳读取后打开对应 localhost 页面。
"""
from __future__ import annotations

import os
import socket
import sys


def _pick_port(preferred: int = 8756) -> int:
    """优先用 preferred 端口;被占用则让 OS 分配一个空闲端口。"""
    for port in (preferred,):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                break
    # preferred 被占:要一个随机空闲口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    # ---- 冻结环境:强制单机 local 模式 ----
    os.environ.setdefault("APP_MODE", "local")
    os.environ.setdefault("APP_ENV", "dev")  # local 不用 JWT,放行弱默认
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

    # ---- 数据目录:SQLite 落用户可写目录(打包目录只读)----
    # 延迟导入:app.paths 不依赖重模块,先把 DATABASE_URL 定好再导 app.*
    from app.paths import user_data_dir

    data_dir = user_data_dir()
    db_path = (data_dir / "jarvis_write.db").as_posix()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")

    host = "127.0.0.1"
    port = int(os.environ.get("JARVIS_PORT", "0")) or _pick_port()

    # 桌面壳约定:读这一行拿到实际地址后打开窗口
    print(f"JARVIS_SERVER_URL=http://{host}:{port}", flush=True)

    import uvicorn

    from app.main import app  # 触发建表/迁移在 lifespan 里跑

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    # PyInstaller 冻结的多进程兜底(uvicorn 单进程模式不 fork,但保险)
    if getattr(sys, "frozen", False):
        import multiprocessing

        multiprocessing.freeze_support()
    main()
