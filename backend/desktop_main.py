# desktop_main.py
# -*- coding: utf-8 -*-
"""桌面版(PyInstaller 冻结)入口:单机 local 模式,免登录,数据落用户目录。

与服务器版的区别全部在启动前用环境变量拍定,app 代码零分叉:
- APP_MODE=local        → 免登录、单用户、放行本机源(见 app/auth.py、main.py)
- DATABASE_URL          → SQLite 落 %APPDATA%\\jarvis-write\\jarvis_write.db
- JARVIS_BIND_HOST      → 只监听 127.0.0.1(免鉴权的安全前提,main.py 强制校验)
- JWT_SECRET            → 机器级随机密钥(见 _machine_secret),LLM key 加密密钥由此派生

端口默认 8756(避开常见占用),被占用时顺延找空闲口;实际端口打印到 stdout,
供桌面壳读取后打开对应 localhost 页面。
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def _port_free(host: str, port: int) -> bool:
    """探测端口在 host 上是否可绑定(空闲)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(host: str, preferred: int = 8756) -> int:
    """优先用 preferred 端口;被占用则让 OS 分配一个空闲端口。"""
    if _port_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _load_app_version() -> None:
    """把打包时写入的 _version.txt 读进 APP_VERSION 环境变量。

    冻结后进程不继承 CI 的环境变量,所以版本号得随包带一个文件。资源经
    resource_path 定位(冻结:_MEIPASS/_version.txt;源码:backend/_version.txt)。
    文件缺失或读失败一律忽略,APP_VERSION 保持未设(下游回落 "dev")。
    """
    if os.environ.get("APP_VERSION"):
        return
    try:
        from app.paths import resource_path

        v = resource_path("_version.txt").read_text(encoding="utf-8").strip()
        if v:
            os.environ["APP_VERSION"] = v
    except OSError:
        pass


def _machine_secret(data_dir: Path) -> tuple[str, bool]:
    """读取/首启生成机器级随机密钥,返回 (secret, 是否本次新建)。

    用途:crypto.py 从 JWT_SECRET 派生 Fernet 密钥加密 LLM key。桌面版此前
    用仓库里公开的 DEFAULT_JWT_SECRET → 任何人拿到 db 文件就能解密全部
    LLM key,"加密"形同虚设。改为首启生成随机密钥落数据目录,之后稳定复用。

    诚实的威胁模型:密钥文件与 db 同目录,防的是"只泄露 db"(备份外泄、
    同步盘误传)而非"整台机器被读"。本地多用户场景请依赖 OS 账户隔离。
    """
    path = data_dir / ".machine-secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing, False
    except OSError:
        pass

    import secrets

    secret = secrets.token_urlsafe(48)
    path.write_text(secret, encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # POSIX 生效;Windows ACL 不完全遵循,尽力而为
    except OSError:
        pass
    return secret, True


def _reencrypt_legacy_keys(new_secret: str) -> None:
    """存量 LLM key 从"默认弱密钥"重加密到机器级密钥(幂等,仅首启跑一次)。

    老版本的 api_key 密文用仓库公开的 DEFAULT_JWT_SECRET 派生密钥加密;切到
    机器级密钥后这些密文解不开(会被当成未配置,用户得重填)。此处用旧密钥解、
    新密钥重加密写回;两种密钥都解不开的行原样跳过(本就是坏数据)。
    """
    import base64
    import hashlib
    import logging

    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import inspect, text

    from app.config import DEFAULT_JWT_SECRET
    from app.crypto import ENC_PREFIX
    from app.db.session import engine

    log = logging.getLogger("jarvis-write")

    def fernet_of(secret: str) -> Fernet:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)

    old_f, new_f = fernet_of(DEFAULT_JWT_SECRET), fernet_of(new_secret)

    with engine.begin() as conn:
        insp = inspect(conn)
        if "provider_settings" not in insp.get_table_names():
            return
        rows = conn.execute(text("SELECT id, api_key FROM provider_settings")).fetchall()
        migrated = 0
        for row_id, api_key in rows:
            if not api_key or not api_key.startswith(ENC_PREFIX):
                continue
            token = api_key[len(ENC_PREFIX) :].encode("ascii")
            try:
                new_f.decrypt(token)
                continue  # 已是新密钥加密(理论上首启不会有),跳过
            except InvalidToken:
                pass
            try:
                plain = old_f.decrypt(token).decode("utf-8")
            except InvalidToken:
                continue  # 坏数据,原样保留,界面上表现为"未配置"
            re_enc = ENC_PREFIX + new_f.encrypt(plain.encode("utf-8")).decode("ascii")
            conn.execute(
                text("UPDATE provider_settings SET api_key = :k WHERE id = :i"),
                {"k": re_enc, "i": row_id},
            )
            migrated += 1
        if migrated:
            log.info("迁移:%d 条 LLM key 已重加密到机器级密钥", migrated)


def main() -> None:
    # ---- 冻结环境:强制单机 local 模式 ----
    os.environ.setdefault("APP_MODE", "local")
    os.environ.setdefault("APP_ENV", "dev")  # local 不用 JWT 签名,放行弱默认校验
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
    # 桌面入口标记:app/main.py 的 _assert_local_safe 据此放行 local 模式。
    # 没有这个标记而 APP_MODE=local 会被拒绝启动,防止公网服务误开免鉴权。
    os.environ["JARVIS_LAUNCHER"] = "desktop"
    # 绑定地址显式拍定:_assert_local_safe 强制校验它必须是回环地址,
    # 防止"设了标记却 --host 0.0.0.0"的裸奔组合。
    host = "127.0.0.1"
    os.environ["JARVIS_BIND_HOST"] = host

    # 应用版本:冻结环境变量不保留,故打包时把版本写进 _version.txt(spec 打入),
    # 启动时读回设进 APP_VERSION,供 /api/version 返回给设置页「关于」显示。
    # 未打入(旧包/开发)则维持 "dev",不影响启动。
    _load_app_version()

    # ---- 数据目录:SQLite 落用户可写目录(打包目录只读)----
    # 延迟导入:app.paths 不依赖重模块,先把 DATABASE_URL 定好再导 app.*
    from app.paths import user_data_dir

    data_dir = user_data_dir()
    db_path = (data_dir / "jarvis_write.db").as_posix()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")

    # ---- LLM key 加密密钥:机器级随机密钥,取代仓库公开的弱默认值 ----
    # 必须在任何 app.* 导入前拍定 JWT_SECRET(get_settings 有 lru_cache)。
    secret, secret_created = _machine_secret(data_dir)
    os.environ["JWT_SECRET"] = secret

    # 老安装升级:首启新建密钥时,把默认弱密钥加密的存量 key 重加密过来,
    # 用户无感;新装则无数据可迁,直接跳过。
    if secret_created:
        _reencrypt_legacy_keys(secret)

    env_port = int(os.environ.get("JARVIS_PORT", "0")) or 0
    if env_port:
        # 显式指定的端口被占用:清晰报错退出(桌面壳会把启动失败弹给用户),
        # 而不是打印 URL 后让壳导航到死地址白屏。
        if not _port_free(host, env_port):
            print(
                f"端口 {env_port} 已被占用(JARVIS_PORT 指定),无法启动。"
                "请关闭占用进程或取消 JARVIS_PORT 让系统自动选择端口。",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        port = env_port
    else:
        port = _pick_port(host)

    # 桌面壳约定:读这一行拿到实际地址;壳侧会轮询端口就绪后再开窗口,
    # 因此此处打印早于 uvicorn bind 完成没有害处。
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
