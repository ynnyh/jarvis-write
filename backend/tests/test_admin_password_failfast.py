# tests/test_admin_password_failfast.py
# -*- coding: utf-8 -*-
"""启动自检(之二):非 dev 环境拒绝以弱默认管理员口令启动。

初始管理员口令的默认值(admin/admin12345)是公开信息,任何扫到 8000 端口的人
第一个试的就是它。自检与弱 JWT 密钥同一条线(见 main._assert_secure_config),
判定读 Settings 的字段默认值,不在代码里复写第二份字符串。
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from app.config import Settings, get_settings
from app.main import _assert_secure_config


def test_prod_rejects_default_admin_password():
    default_pwd = Settings.model_fields["admin_password"].default
    with patch.object(get_settings(), "app_env", "prod"), \
         patch.object(get_settings(), "jwt_secret", "a-long-random-production-secret-xyz"), \
         patch.object(get_settings(), "admin_password", default_pwd):
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            _assert_secure_config()


def test_prod_accepts_custom_admin_password():
    """两项都换掉 → prod 放行(这条自检不误伤正常生产部署)。"""
    settings = get_settings()
    with patch.object(settings, "app_env", "prod"), \
         patch.object(settings, "jwt_secret", "a-long-random-production-secret-xyz"), \
         patch.object(settings, "admin_password", "a-strong-admin-pw-xyz"):
        _assert_secure_config()  # 不抛


def test_dev_allows_default_admin_password():
    """默认 dev + 默认口令 → 放行(本地开发/测试不被打扰)。"""
    settings = get_settings()
    with patch.object(settings, "app_env", "dev"):
        _assert_secure_config()  # 不抛


def test_jwt_check_fires_before_admin_password():
    """两项都是弱默认时,先报 JWT_SECRET——旧的自检文案不变(排障时先看这条)。"""
    from app.config import DEFAULT_JWT_SECRET

    settings = get_settings()
    with patch.object(settings, "app_env", "prod"), \
         patch.object(settings, "jwt_secret", DEFAULT_JWT_SECRET), \
         patch.object(settings, "admin_password", Settings.model_fields["admin_password"].default):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            _assert_secure_config()


def test_check_reads_field_default_not_hardcoded_string():
    """判定必须读 Settings 字段元数据:两处各写一份字符串,改一处就等于自检失效。"""
    src = inspect.getsource(_assert_secure_config)
    assert 'model_fields["admin_password"]' in src
    assert "settings.admin_password ==" in src
