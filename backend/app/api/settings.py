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
from app.crypto import encrypt
from app.db.models import ProviderSetting, User
from app.db.session import get_db
from app.net_guard import assert_public_base_url
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
    assert_public_base_url(req.base_url)  # SSRF 防线:拒绝指向内网/本机的 base_url
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
        row.api_key = encrypt(req.api_key.strip())
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


class DeleteResult(BaseModel):
    # deleted=True:已删除;False:配置连通正常,需前端二次确认(needs_confirm=True)
    deleted: bool
    needs_confirm: bool = False
    reason: str = ""


async def _provider_alive(name: str) -> bool:
    """现场探测某聊天 provider 是否连通(删除前的二次确认判定)。
    任何失败/未配置都视为不连通(可直接删),不抛异常。"""
    try:
        if not resolve_provider_config(name)["api_key"]:
            return False
        adapter = create_llm_adapter(name, max_tokens=32, timeout=30)
        await adapter.complete(adapter.to_messages("ping"))
        return True
    except Exception:  # noqa: BLE001 — 探测失败即视为不连通,允许直接删
        return False


@router.delete("/providers/{name}", response_model=DeleteResult)
async def delete_provider_setting(
    name: str,
    confirmed: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除某 provider 的配置行(清空,回落 .env / 默认来源)。

    交互约定(见需求):连不通的配置允许直接删;已确认连通的配置需二次确认。
    - confirmed=False(默认):先现场探测一次连通性。
        · 不连通 → 直接删,deleted=True
        · 连通   → 不删,返回 needs_confirm=True,由前端弹窗确认
    - confirmed=True:跳过探测,直接删(前端确认后带此参数重发)。
    """
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
        # 没有存过配置:视为已是空态,幂等返回成功
        return DeleteResult(deleted=True)

    if not confirmed:
        if await _provider_alive(name):
            return DeleteResult(
                deleted=False,
                needs_confirm=True,
                reason="该配置当前连接正常,确认要删除吗?",
            )

    db.delete(row)
    db.commit()
    return DeleteResult(deleted=True)


@router.post("/providers/{name}/test", response_model=TestResult)
async def test_provider(name: str, user: User = Depends(get_current_user)):
    """用当前已存配置实际调一次模型(设置页的「测试连接」按钮)。"""
    name = name.lower()
    if name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知 provider: {name}")

    cfg = resolve_provider_config(name)
    if not cfg["api_key"]:
        return TestResult(ok=False, provider=name, error="尚未配置 api_key")

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
        )
    except Exception as exc:  # noqa: BLE001 — 测试接口,错误原样反馈给用户
        return TestResult(ok=False, provider=name, error=str(exc)[:500])
