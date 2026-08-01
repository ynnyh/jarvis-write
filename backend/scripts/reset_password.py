# backend/scripts/reset_password.py
# -*- coding: utf-8 -*-
"""脱机重置用户密码:不要求旧密码,直接落库新哈希。

适用场景:
  - 部署时 ADMIN_PASSWORD 设成了复杂随机串,登录靠浏览器记住,
    设置页"修改密码"要求输旧密码,人根本敲不出来;
  - 干脆忘了密码,登录都进不去。

安全前提:执行本脚本需要对宿主机/容器的 shell 访问权
(docker exec 或本机终端),能摸到库文件的人本就能改库,
所以这不是新的攻击面,只是把"手动改库"产品化。

用法:
    cd backend
    .venv/Scripts/python -m scripts.reset_password --user admin
      # 不带 --password 则交互输入(不回显)
    .venv/Scripts/python -m scripts.reset_password --user admin --password '新密码'

Docker 部署(容器内工作目录即 backend 等价路径):
    docker exec -it <容器名> python -m scripts.reset_password --user admin

退出码 0 = 成功;非 0 = 失败(用户不存在/密码不合规等)。
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

# 复用应用的引擎/模型:DATABASE_URL 等环境变量照常生效,
# 与运行中的服务指向同一个库。
from app.auth import hash_password
from app.config import get_settings
from app.db.session import SessionLocal
from app.db.models.user import User

MIN_LEN, MAX_LEN = 6, 128  # 与注册/改密接口同一规则
_BCRYPT_MAX_BYTES = 72


def _validate(new_password: str) -> str | None:
    """返回错误文案;None 表示通过。"""
    if not (MIN_LEN <= len(new_password) <= MAX_LEN):
        return f"密码长度需在 {MIN_LEN}-{MAX_LEN} 位之间"
    if len(new_password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        return "密码过长(bcrypt 上限 72 字节,约 24 个汉字),请缩短后再试"
    return None


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="脱机重置用户密码(无需旧密码)")
    parser.add_argument(
        "--user", default=settings.admin_username,
        help=f"用户名(默认:{settings.admin_username})",
    )
    parser.add_argument("--password", default=None, help="新密码(省略则交互输入)")
    args = parser.parse_args()

    new_password = args.password or getpass.getpass("新密码: ")
    if not args.password:
        if getpass.getpass("再输一次: ") != new_password:
            print("两次输入不一致,已取消")
            return 1

    if err := _validate(new_password):
        print(f"密码不合规:{err}")
        return 1

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.user))
        if user is None:
            names = db.scalars(select(User.username)).all()
            print(f"用户 {args.user!r} 不存在;现有用户:{', '.join(names) or '(空)'}")
            return 1
        user.password_hash = hash_password(new_password)
        db.commit()

    print(f"已重置用户 {args.user!r} 的密码,可立即用新密码登录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
