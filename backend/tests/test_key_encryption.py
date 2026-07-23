# tests/test_key_encryption.py
# -*- coding: utf-8 -*-
"""per-user LLM key 加密:往返 / 明文兼容 / 落库为密文 / 存量迁移。"""
import pytest
from fastapi.testclient import TestClient

from app.crypto import ENC_PREFIX, decrypt, encrypt
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------- 单元:crypto ----------

def test_encrypt_decrypt_roundtrip():
    plain = "sk-secret-key-123"
    enc = encrypt(plain)
    assert enc.startswith(ENC_PREFIX)
    assert enc != plain
    assert decrypt(enc) == plain


def test_empty_stays_empty():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_decrypt_passes_through_legacy_plaintext():
    # 历史明文(无前缀)原样返回,兼容旧数据
    assert decrypt("sk-legacy-plain") == "sk-legacy-plain"


def test_decrypt_corrupt_returns_empty():
    assert decrypt(ENC_PREFIX + "not-a-valid-token") == ""


# ---------- 接口:落库为密文,用时解密 ----------

def _auth(client: TestClient, username: str) -> tuple[dict, int]:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    uid = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, uid


def test_saved_key_is_ciphertext_in_db(client):
    from app.db.models import ProviderSetting
    from app.db.session import SessionLocal

    headers, uid = _auth(client, "enc_user")
    r = client.put(
        "/api/settings/providers/deepseek",
        headers=headers,
        json={
            "api_key": "sk-plaintext-abc",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "is_default": True,
        },
    )
    assert r.status_code == 200, r.text

    # 库里存的是密文
    db = SessionLocal()
    try:
        row = (
            db.query(ProviderSetting)
            .filter(
                ProviderSetting.user_id == uid,
                ProviderSetting.provider == "deepseek",
            )
            .first()
        )
        assert row.api_key.startswith(ENC_PREFIX)
        assert "sk-plaintext-abc" not in row.api_key
        assert decrypt(row.api_key) == "sk-plaintext-abc"
    finally:
        db.close()

    # 设置页返回的是打码后的明文(has_key 为真)
    body = client.get("/api/settings/providers", headers=headers).json()
    deepseek = next(p for p in body if p["provider"] == "deepseek")
    assert deepseek["has_key"] is True
    assert "sk-plaintext-abc" not in deepseek["api_key_masked"]


def test_factory_resolves_decrypted_key(client):
    """工厂在该用户上下文下拿到的是解密后的明文 key。"""
    from app.auth import current_user_id
    from app.llm.factory import resolve_provider_config

    headers, uid = _auth(client, "enc_factory_user")
    client.put(
        "/api/settings/providers/openai",
        headers=headers,
        json={"api_key": "sk-openai-xyz", "base_url": "", "model": "", "is_default": True},
    )
    tok = current_user_id.set(uid)
    try:
        cfg = resolve_provider_config("openai")
    finally:
        current_user_id.reset(tok)
    assert cfg["api_key"] == "sk-openai-xyz"


# ---------- 迁移:存量明文补加密 ----------

def test_migration_encrypts_existing_plaintext(client):
    from app.db.models import ProviderSetting
    from app.db.session import SessionLocal
    from app.migrate import _encrypt_existing_keys

    # 直接落一条明文 key(模拟加密上线前的存量行)
    db = SessionLocal()
    try:
        row = ProviderSetting(
            provider="gemini", user_id=99999, api_key="sk-legacy-raw"
        )
        db.add(row)
        db.commit()
        row_id = row.id
    finally:
        db.close()

    _encrypt_existing_keys()

    db = SessionLocal()
    try:
        row = db.get(ProviderSetting, row_id)
        assert row.api_key.startswith(ENC_PREFIX)
        assert decrypt(row.api_key) == "sk-legacy-raw"
        first_cipher = row.api_key
    finally:
        db.close()

    # 幂等:再跑一次不会重复加密(已带前缀跳过)
    _encrypt_existing_keys()
    db = SessionLocal()
    try:
        assert db.get(ProviderSetting, row_id).api_key == first_cipher
    finally:
        db.close()
