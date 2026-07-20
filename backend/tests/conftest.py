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
# 删除项目时会清 Chroma 向量集合,同样指向临时目录
os.environ["CHROMA_PERSIST_DIR"] = f"{_TMPDIR}/chroma"
