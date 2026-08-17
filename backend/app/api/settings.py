# app/api/settings.py
# -*- coding: utf-8 -*-
"""站点设置接口:LLM 模型配置(cc-switch 风格,每用户多套命名配置)。

GET    /api/settings/providers                 全部配置(key 打码)
POST   /api/settings/providers                 新增一套配置(首个自动设为默认)
PUT    /api/settings/providers/{id}            更新(可带 is_default/is_default_fast 一键切换)
DELETE /api/settings/providers/{id}            删除(连通需二次确认)
POST   /api/settings/providers/{id}/test       用该配置实际调一次模型
GET    /api/settings/providers/status          是否已配置任一可用模型(引导横幅用)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import encrypt
from app.db.models import ProviderConfig, User
from app.db.session import get_db
from app.net_guard import assert_public_base_url
from app.llm.factory import (
    _REGISTRY,
    available_providers,
    create_llm_adapter,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 各协议默认 base_url / 模型,前端「添加配置」预填与占位用。
# openai-compatible 是主力通用卡,占位给最常见的 OpenAI 端点作示例(实际由用户
# 或前端快捷预设改成中转站/DeepSeek/Kimi/本地 Ollama 等);deepseek/openai 是其别名。
_PRESETS = {
    "openai-compatible": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
    },
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


class ProviderConfigOut(BaseModel):
    id: int
    name: str
    interface_format: str
    api_key_masked: str
    has_key: bool
    base_url: str
    model: str
    timeout: int
    max_tokens: int
    is_default: bool
    is_default_fast: bool
    default_base_url: str
    default_model: str


class ProviderConfigIn(BaseModel):
    name: str = ""
    interface_format: str = "openai-compatible"
    api_key: str | None = Field(
        default=None, description="留空/不传 = 不改动已存的 key(仅更新时)"
    )
    base_url: str = ""
    model: str = ""
    timeout: int = Field(default=0, ge=0, le=3600, description="0=跟随全局")
    max_tokens: int = Field(default=0, ge=0, le=200000, description="0=跟随全局/任务默认")
    is_default: bool | None = None
    is_default_fast: bool | None = None


def _out(row: ProviderConfig, plain_key: str) -> ProviderConfigOut:
    preset = _PRESETS.get(row.interface_format, {"base_url": "", "model": ""})
    return ProviderConfigOut(
        id=row.id,
        name=row.name,
        interface_format=row.interface_format,
        api_key_masked=_mask(plain_key),
        has_key=bool(plain_key),
        base_url=row.base_url,
        model=row.model,
        timeout=row.timeout or 0,
        max_tokens=row.max_tokens or 0,
        is_default=row.is_default,
        is_default_fast=row.is_default_fast,
        default_base_url=preset["base_url"],
        default_model=preset["model"],
    )


def _get_row(db: Session, user: User, config_id: int) -> ProviderConfig:
    row = (
        db.query(ProviderConfig)
        .filter(ProviderConfig.id == config_id, ProviderConfig.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在。")
    return row


def _check_format(interface_format: str) -> str:
    fmt = interface_format.lower()
    if fmt not in _REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"未知协议: {interface_format},可选: {list(_REGISTRY)}",
        )
    return fmt


def _apply_default_flags(
    db: Session, user: User, row: ProviderConfig, req: ProviderConfigIn
) -> None:
    """默认/快档标记全用户唯一:置 True 时先清掉其他配置的同名标记。"""
    if req.is_default:
        db.query(ProviderConfig).filter(
            ProviderConfig.user_id == user.id, ProviderConfig.id != row.id
        ).update({ProviderConfig.is_default: False}, synchronize_session=False)
        row.is_default = True
    elif req.is_default is False:
        row.is_default = False
    if req.is_default_fast:
        db.query(ProviderConfig).filter(
            ProviderConfig.user_id == user.id, ProviderConfig.id != row.id
        ).update({ProviderConfig.is_default_fast: False}, synchronize_session=False)
        row.is_default_fast = True
    elif req.is_default_fast is False:
        row.is_default_fast = False


class ProviderStatus(BaseModel):
    configured: bool
    providers: dict[str, bool]


@router.get("/providers/status", response_model=ProviderStatus)
async def provider_status(user: User = Depends(get_current_user)):
    """当前用户是否配置了至少一个可用的 LLM 配置(DB key 或 .env 兜底)。

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


@router.get("/providers", response_model=list[ProviderConfigOut])
async def list_provider_configs(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from app.crypto import decrypt

    rows = (
        db.query(ProviderConfig)
        .filter(ProviderConfig.user_id == user.id)
        .order_by(ProviderConfig.id)
        .all()
    )
    return [_out(r, decrypt(r.api_key)) for r in rows]


@router.post("/providers", response_model=ProviderConfigOut)
async def create_provider_config(
    req: ProviderConfigIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fmt = _check_format(req.interface_format)
    assert_public_base_url(req.base_url)  # SSRF 防线:拒绝指向内网/本机的 base_url

    row = ProviderConfig(
        user_id=user.id,
        name=req.name.strip() or _PRESETS.get(fmt, {}).get("model", fmt),
        interface_format=fmt,
        api_key=encrypt((req.api_key or "").strip()),
        base_url=req.base_url.strip(),
        model=req.model.strip(),
        timeout=req.timeout,
        max_tokens=req.max_tokens,
    )
    db.add(row)
    db.flush()

    # 首套配置自动成为默认;显式传了标记则以标记为准
    has_other = (
        db.query(ProviderConfig)
        .filter(ProviderConfig.user_id == user.id, ProviderConfig.id != row.id)
        .count()
        > 0
    )
    if req.is_default is None and not has_other:
        row.is_default = True
    else:
        _apply_default_flags(db, user, row, req)

    db.commit()
    from app.crypto import decrypt

    return _out(row, decrypt(row.api_key))


@router.put("/providers/{config_id}", response_model=ProviderConfigOut)
async def update_provider_config(
    config_id: int,
    req: ProviderConfigIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _get_row(db, user, config_id)
    fmt = _check_format(req.interface_format)
    assert_public_base_url(req.base_url)

    row.name = req.name.strip() or row.name
    row.interface_format = fmt
    # 空串/不传 = 不改动已存 key
    if req.api_key:
        row.api_key = encrypt(req.api_key.strip())
    row.base_url = req.base_url.strip()
    row.model = req.model.strip()
    row.timeout = req.timeout
    row.max_tokens = req.max_tokens
    _apply_default_flags(db, user, row, req)

    db.commit()
    from app.crypto import decrypt

    return _out(row, decrypt(row.api_key))


class DeleteResult(BaseModel):
    # deleted=True:已删除;False:配置连通正常,需前端二次确认(needs_confirm=True)
    deleted: bool
    needs_confirm: bool = False
    reason: str = ""


async def _config_alive(config_id: int) -> bool:
    """现场探测某配置是否连通(删除前的二次确认判定)。
    任何失败/未配置都视为不连通(可直接删),不抛异常。"""
    try:
        adapter = create_llm_adapter(config_id=config_id, max_tokens=32, timeout=30)
        await adapter.complete(adapter.to_messages("ping"))
        return True
    except Exception:  # noqa: BLE001 — 探测失败即视为不连通,允许直接删
        return False


@router.delete("/providers/{config_id}", response_model=DeleteResult)
async def delete_provider_config(
    config_id: int,
    confirmed: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除一套配置。

    交互约定(见需求):连不通的配置允许直接删;已确认连通的配置需二次确认。
    - confirmed=False(默认):先现场探测一次连通性。
        · 不连通 → 直接删,deleted=True
        · 连通   → 不删,返回 needs_confirm=True,由前端弹窗确认
    - confirmed=True:跳过探测,直接删(前端确认后带此参数重发)。
    """
    row = _get_row(db, user, config_id)

    if not confirmed:
        if await _config_alive(config_id):
            return DeleteResult(
                deleted=False,
                needs_confirm=True,
                reason="该配置当前连接正常,确认要删除吗?",
            )

    db.delete(row)
    db.commit()
    return DeleteResult(deleted=True)


@router.post("/providers/{config_id}/test", response_model=TestResult)
async def test_provider_config(
    config_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用该配置实际调一次模型(设置页的「测试连接」按钮)。"""
    row = _get_row(db, user, config_id)

    from app.crypto import decrypt

    if not decrypt(row.api_key):
        return TestResult(
            ok=False, provider=row.interface_format, error="尚未配置 api_key"
        )

    adapter = create_llm_adapter(config_id=config_id, max_tokens=100, timeout=60)
    try:
        resp = await adapter.complete(
            adapter.to_messages("请回复:连接成功")
        )
        return TestResult(
            ok=True,
            provider=row.interface_format,
            model=resp.model,
            reply=resp.content[:200],
        )
    except Exception as exc:  # noqa: BLE001 — 测试接口,错误原样反馈给用户
        return TestResult(
            ok=False, provider=row.interface_format, error=str(exc)[:500]
        )
