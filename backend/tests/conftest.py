# tests/conftest.py
# -*- coding: utf-8 -*-
"""pytest 公共配置:独立临时数据库,绝不碰 backend/jarvis_write.db。

必须在任何 app.* 导入之前设置环境变量(Settings 有 lru_cache,
db/session.py 在 import 时建引擎),所以放在 conftest 模块顶层。
"""
import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="jarvis-write-test-")

# 环境变量优先级高于 .env,确保测试用独立库
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"
os.environ["INVITE_CODE"] = "test-invite"
# 关掉全局限流:共享 app 单例会被整个用例集的大量注册/登录打爆(限流单独在
# test_ratelimit.py 用独立 app 验证)。
os.environ["RATE_LIMIT_ENABLED"] = "false"
