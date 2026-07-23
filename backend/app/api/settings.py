# app/api/settings.py
# -*- coding: utf-8 -*-
"""站点设置接口:LLM provider 配置(设置页用)。

GET  /api/settings/providers            三家配置(key 打码)+ 谁是默认
                                        + 末尾一张 embedding 专用卡(伪 provider)
PUT  /api/settings/providers/{name}     保存某家配置(存数据库);name=embedding 为专用 embedding 配置
POST /api/settings/providers/{name}/test  用已存配置实际调一次模型;embedding 卡只测 embed
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt, encrypt
from app.db.models import ProviderSetting, User
from app.db.session import get_db
from app.net_guard import assert_public_base_url
from app.llm.embeddings import (
    EMBEDDING_PROVIDER,
    EmbeddingClient,
    check_embedding,
    resolve_embedding_config,
)
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

# embedding 专用卡的 placeholder(推荐免费/便宜渠道,仅前端展示用)
_EMBEDDING_DEFAULTS = {
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "BAAI/bge-m3",
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
    # 当前生效来源:user=专用配置 / env=环境变量 / default=默认 provider /
    # none=未配置;仅 embedding 卡使用,三家聊天卡恒为 ""
    source: str = ""


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
    # 仅 embedding 卡测试用:生效来源(user/env/default/none)
    source: str = ""


def _effective_source() -> str:
    """当前 embedding 生效来源;default 兜底但没 key 时记为 none。"""
    cfg = resolve_embedding_config()
    if cfg["source"] == "default" and not cfg["api_key"]:
        return "none"
    return cfg["source"]


def _embedding_card(db: Session, user: User) -> ProviderSettingOut:
    """伪 provider "embedding" 的卡片数据:字段取 DB 行(编辑用),
    source 取当前生效来源;is_default 恒 false。"""
    row = (
        db.query(ProviderSetting)
        .filter(
            ProviderSetting.user_id == user.id,
            ProviderSetting.provider == EMBEDDING_PROVIDER,
        )
        .first()
    )
    key_plain = decrypt(row.api_key) if row else ""
    return ProviderSettingOut(
        provider=EMBEDDING_PROVIDER,
        api_key_masked=_mask(key_plain),
        has_key=bool(key_plain),
        base_url=row.base_url if row else "",
        model=row.model if row else "",
        is_default=False,
        default_base_url=_EMBEDDING_DEFAULTS["base_url"],
        default_model=_EMBEDDING_DEFAULTS["model"],
        source=_effective_source(),
    )


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
    # 列表末尾追加 embedding 专用卡(伪 provider,不进 LLM 注册表)
    out.append(_embedding_card(db, user))
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
    if name == EMBEDDING_PROVIDER:
        # 伪 provider:专用 embedding 配置;is_default 无意义,忽略
        row = (
            db.query(ProviderSetting)
            .filter(
                ProviderSetting.user_id == user.id,
                ProviderSetting.provider == EMBEDDING_PROVIDER,
            )
            .first()
        )
        if row is None:
            row = ProviderSetting(provider=EMBEDDING_PROVIDER, user_id=user.id)
            db.add(row)

        # 与聊天卡同一语义:空串/不传 = 不改动已存 key,纯空白 = 清除 key
        if req.api_key is not None and req.api_key != "":
            row.api_key = encrypt(req.api_key.strip())
        row.base_url = req.base_url.strip()
        row.model = req.model.strip()
        row.is_default = False
        db.commit()
        return _embedding_card(db, user)

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


async def _embedding_alive() -> bool:
    """现场探测 embedding 专用配置是否连通,判定逻辑同上。"""
    try:
        cfg = resolve_embedding_config()
        if cfg["source"] != "user" or not cfg["api_key"]:
            # 只对"专用配置"负责;回落来源的连通性不该拦删除
            return False
        await EmbeddingClient(timeout=30).embed(["测试"])
        return True
    except Exception:  # noqa: BLE001
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
    is_embedding = name == EMBEDDING_PROVIDER
    if not is_embedding and name not in _REGISTRY:
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
        alive = await (_embedding_alive() if is_embedding else _provider_alive(name))
        if alive:
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
    """用当前已存配置实际调一次模型(设置页的「测试连接」按钮)。

    顺带探测该 provider 的 /embeddings 可用性(设置页据此提示语义记忆
    是否降级);embedding 探测失败不影响聊天测试结果。
    """
    name = name.lower()
    if name == EMBEDDING_PROVIDER:
        # embedding 专用卡:用保存后生效的解析结果发一次真实 embed,没有聊天测试
        cfg = resolve_embedding_config()
        source = _effective_source()
        if not cfg["api_key"]:
            return TestResult(
                ok=False,
                provider=name,
                model=cfg["model"],
                error="尚未配置 embedding api_key",
                source=source,
            )
        client = EmbeddingClient(timeout=30)
        try:
            await client.embed(["测试"])
            return TestResult(
                ok=True, provider=name, model=client.model, source=client.source
            )
        except Exception as exc:  # noqa: BLE001 — 测试接口,错误原样反馈给用户
            return TestResult(
                ok=False,
                provider=name,
                model=client.model,
                error=str(exc)[:500],
                source=client.source,
            )

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
