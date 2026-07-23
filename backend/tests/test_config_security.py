# tests/test_config_security.py
# -*- coding: utf-8 -*-
"""P1-⑤ 启动自检:生产环境拒绝以弱默认 JWT 密钥启动(非 compose 启动的兜底)。

弱 jwt_secret 可被伪造任意 user_id 的 token 接管账号,故 APP_ENV=prod 下用默认值
即拒启动;dev(默认,含本测试与全部单测)放行,不打扰本地开发。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_prod_rejects_default_jwt_secret():
    from app.config import DEFAULT_JWT_SECRET, get_settings
    from app.main import _assert_secure_config

    settings = get_settings()
    with patch.object(settings, "app_env", "prod"), \
         patch.object(settings, "jwt_secret", DEFAULT_JWT_SECRET):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            _assert_secure_config()


def test_prod_accepts_custom_secret():
    from app.config import get_settings
    from app.main import _assert_secure_config

    settings = get_settings()
    with patch.object(settings, "app_env", "prod"), \
         patch.object(settings, "jwt_secret", "a-long-random-production-secret-xyz"):
        _assert_secure_config()  # 不抛


def test_dev_allows_default_secret():
    """默认 dev + 默认弱密钥 → 放行(本地开发/测试不被打扰)。"""
    from app.main import _assert_secure_config

    _assert_secure_config()  # get_settings() 默认 app_env=dev
