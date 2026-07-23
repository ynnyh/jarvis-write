# app/crypto.py
# -*- coding: utf-8 -*-
"""per-user LLM key 的 at-rest 对称加密。

动机:用户自带 key 存在我们库里 = 我们替他们保管;DB 文件一旦泄露(拖库 / 备份
外泄),明文 key 会连带泄露。用 Fernet(AES-128-CBC + HMAC)加密后再落库。

密钥来源:从 JWT_SECRET 派生(生产已强制随机长串,见 main._assert_secure_config),
不新增必配环境变量。代价:换 JWT_SECRET 会使已存的加密 key 无法解密,需在设置页
重填——运维改密钥时注意(见 docs)。

兼容旧明文:历史行是明文 key,decrypt 见到没有 ENC_PREFIX 的值就原样返回;
迁移脚本 _encrypt_existing_keys 会把它们逐个加密回写(幂等)。
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# 加密值前缀:区分"已加密" vs "历史明文",避免把明文误当密文去解密
ENC_PREFIX = "enc:v1:"


@lru_cache
def _fernet() -> Fernet:
    secret = get_settings().jwt_secret.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt(plain: str) -> str:
    """加密明文 key;空串原样返回(空 = 未配置,不该变成一段密文)。"""
    if not plain:
        return plain
    token = _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(stored: str) -> str:
    """解密;空串或历史明文(无前缀)原样返回;密文损坏/密钥变更返回空串(视为未配置)。"""
    if not stored:
        return stored
    if not stored.startswith(ENC_PREFIX):
        return stored  # 历史明文,兼容
    token = stored[len(ENC_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # 密钥变了或数据损坏:当作未配置(回落 .env / 提示重配),不炸整个请求
        return ""
