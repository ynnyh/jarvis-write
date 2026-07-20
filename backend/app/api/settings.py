# app/api/settings.py
# -*- coding: utf-8 -*-
"""站点设置接口:LLM provider 配置(设置页用)。

GET  /api/settings/providers            三家配置(key 打码)+ 谁是默认
PUT  /api/settings/providers/{name}     保存某家配置(存数据库)
POST /api/settings/providers/{name}/test  用已存配置实际调一次模型
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import ProviderSetting, User
from app.db.session import get_db
from app.llm.embeddings import check_embedding
from app.llm.factory import (
    _REGISTRY,
    available_providers,
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 各家默认 base_url / 模型,前端「恢复默认」用
_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
    },
}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


class ProviderSettingOut(BaseModel):
    provider: str
    api_key_masked: str
    has_key: bool
    base_url: str
    model: str
    is_default: bool
    default_base_url: str
    default_model: str


class ProviderSettingIn(BaseModel):
    api_key: str | None = Field(
        default=None, description="留空/不传 = 不改动已存的 key"
    )
    base_url: str = ""
    model: str = ""
    is_default: bool = False


class ProviderStatus(BaseModel):
    configured: bool
    providers: dict[str, bool]


@router.get("/providers/status", response_model=ProviderStatus)
async def provider_status(user: User = Depends(get_current_user)):
    """当前用户是否配置了至少一个可用的 LLM provider(DB key 或 .env 兜底)。

    前端登录后据此显示「未配置模型」的全局引导横幅。
    """
    providers = available_providers()
    return ProviderStatus(configured=any(providers.values()), providers=providers)


class TestResult(BaseModel):
    ok: bool
    provider: str
    model: str = ""
    reply: str = ""
    error: str = ""
    embedding_ok: bool = False
    embedding_error: str = ""


@router.get("/providers", response_model=list[ProviderSettingOut])
async def list_provider_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    default = resolve_default_provider()
    out = []
    for name in _REGISTRY:
        cfg = resolve_provider_config(name)
        out.append(
            ProviderSettingOut(
                provider=name,
                api_key_masked=_mask(cfg["api_key"]),
                has_key=bool(cfg["api_key"]),
                base_url=cfg["base_url"],
                model=cfg["model"],
                is_default=(name == default),
                default_base_url=_DEFAULTS[name]["base_url"],
                default_model=_DEFAULTS[name]["model"],
            )
        )
    return out


@router.put("/providers/{name}", response_model=ProviderSettingOut)
async def save_provider_setting(
    name: str,
    req: ProviderSettingIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = name.lower()
    if name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知 provider: {name}")

    row = (
        db.query(ProviderSetting)
        .filter(
            ProviderSetting.user_id == user.id,
            ProviderSetting.provider == name,
        )
        .first()
    )
    if row is None:
        row = ProviderSetting(provider=name, user_id=user.id)
        db.add(row)

    # 入库前 strip;空串/不传 = 不改动已存 key,纯空白 = 清除 key(存空串,回落 .env)
    if req.api_key is not None and req.api_key != "":
        row.api_key = req.api_key.strip()
    row.base_url = req.base_url.strip()
    row.model = req.model.strip()

    if req.is_default:
        # 只允许一个默认:先清掉本用户别家的
        db.query(ProviderSetting).filter(
            ProviderSetting.user_id == user.id,
            ProviderSetting.provider != name,
        ).update({ProviderSetting.is_default: False})
        row.is_default = True
    else:
        row.is_default = False

    db.commit()

    cfg = resolve_provider_config(name)
    return ProviderSettingOut(
        provider=name,
        api_key_masked=_mask(cfg["api_key"]),
        has_key=bool(cfg["api_key"]),
        base_url=cfg["base_url"],
        model=cfg["model"],
        is_default=(name == resolve_default_provider()),
        default_base_url=_DEFAULTS[name]["base_url"],
        default_model=_DEFAULTS[name]["model"],
    )


@router.post("/providers/{name}/test", response_model=TestResult)
async def test_provider(name: str, user: User = Depends(get_current_user)):
    """用当前已存配置实际调一次模型(设置页的「测试连接」按钮)。

    顺带探测该 provider 的 /embeddings 可用性(设置页据此提示语义记忆
    是否降级);embedding 探测失败不影响聊天测试结果。
    """
    name = name.lower()
    if name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知 provider: {name}")

    cfg = resolve_provider_config(name)
    if not cfg["api_key"]:
        return TestResult(
            ok=False,
            provider=name,
            error="尚未配置 api_key",
            embedding_error="尚未配置 api_key",
        )

    # embedding 探测(最多 ~10s,永不抛异常)
    emb_ok, emb_err = await check_embedding(name)

    adapter = create_llm_adapter(name, max_tokens=100, timeout=60)
    try:
        resp = await adapter.complete(
            adapter.to_messages("请回复:连接成功")
        )
        return TestResult(
            ok=True,
            provider=name,
            model=resp.model,
            reply=resp.content[:200],
            embedding_ok=emb_ok,
            embedding_error=emb_err,
        )
    except Exception as exc:  # noqa: BLE001 — 测试接口,错误原样反馈给用户
        return TestResult(
            ok=False,
            provider=name,
            error=str(exc)[:500],
            embedding_ok=emb_ok,
            embedding_error=emb_err,
        )
