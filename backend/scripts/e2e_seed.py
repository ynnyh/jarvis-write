# -*- coding: utf-8 -*-
"""e2e 冒烟的种子数据:对临时库建表 + 建一个固定测试用户。

只被 frontend/e2e 的 Playwright 后端 webServer 命令链调用
(``python scripts/e2e_seed.py && python -m uvicorn ...``),不参与生产流程。

- 库路径跟服务端同一来源:环境变量 DATABASE_URL(默认本地 jarvis_write.db);
- 直接用应用自身的 create_all + hash_password,不依赖邀请码/HTTP,幂等可重跑;
- 用户名/密码与 e2e 用例约定:e2e_writer / e2e-passw0rd。
"""
import os
import sys

# backend/ 作为 cwd 运行(uvicorn 同款),保证 sqlite:/// 相对路径与 app 导入一致
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

USERNAME = "e2e_writer"
PASSWORD = "e2e-passw0rd"


def _sqlite_path() -> str | None:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    return url[len("sqlite:///"):]


def main() -> None:
    # 每轮全新库:冒烟用例不用做幂等,跑出来的状态永远可预测
    db_file = _sqlite_path()
    if db_file:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(db_file + suffix)
            except FileNotFoundError:
                pass
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(User).filter(User.username == USERNAME).first() is None:
            db.add(User(username=USERNAME, password_hash=hash_password(PASSWORD), is_admin=False))
            db.commit()
    print(f"[e2e_seed] ready: user={USERNAME} db={engine.url}")


if __name__ == "__main__":
    main()
