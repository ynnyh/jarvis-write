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

import pytest


@pytest.fixture(autouse=True)
def _clean_llm_channel_registries():
    """每个用例前后清空 LLM 渠道级进程内登记(_THINKING_REJECTED /
    _REASONING_UNSTOPPABLE)。

    登记是模块级全局,生产里按「渠道+模型」记忆本进程观察到的渠道特性;测试里
    若不清,前一个用例走过的空正文/length 路径会把 (base_url, model) 记进去,
    后面断言精确 max_tokens 推进的用例(如 test_reasoning_empty_content)就被
    凭空加上的思考余量带偏——单跑绿、全量红,典型全局污染。
    """
    from app.llm.base import _REASONING_UNSTOPPABLE, _THINKING_REJECTED

    _THINKING_REJECTED.clear()
    _REASONING_UNSTOPPABLE.clear()
    yield
    _THINKING_REJECTED.clear()
    _REASONING_UNSTOPPABLE.clear()
