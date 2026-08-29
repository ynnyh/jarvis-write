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

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import encrypt
from app.db.models import ProviderConfig, User
from app.db.session import get_db
from app.jobs import normalize_job_error
from app.net_guard import assert_public_base_url, is_cloudflare_hosted
from app.llm.base import EmptyContentError
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
    # 思考模式:空串=跟随全局默认(关);low/high/max=按配置强制
    thinking_mode: str = ""
    is_default: bool
    is_default_fast: bool
    default_base_url: str
    default_model: str
    # base_url 套了 Cloudflare CDN(国内直连常见间歇性失败,前端据此显示提醒条)
    cloudflare: bool = False


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
    thinking_mode: str = Field(
        default="", description="空=跟随全局默认(关思考);low/high/max=强制"
    )
    is_default: bool | None = None
    is_default_fast: bool | None = None


def _norm_thinking_mode(mode: str) -> str:
    """归一到 ''/low/high/max;"off"/"disabled" 视为空串(跟随全局默认=关)。"""
    value = (mode or "").strip().lower()
    if value in ("", "low", "high", "max"):
        return value
    return ""


def _out(row: ProviderConfig, plain_key: str, *, cloudflare: bool = False) -> ProviderConfigOut:
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
        thinking_mode=getattr(row, "thinking_mode", "") or "",
        is_default=row.is_default,
        is_default_fast=row.is_default_fast,
        default_base_url=preset["base_url"],
        default_model=preset["model"],
        cloudflare=cloudflare,
    )


async def _cf_flags(rows: list[ProviderConfig]) -> list[bool]:
    """并行判定各行 base_url 是否套 CF(内部有 DNS,丢线程池防阻塞事件循环)。"""
    if not rows:
        return []
    return list(
        await asyncio.gather(
            *(asyncio.to_thread(is_cloudflare_hosted, r.base_url) for r in rows)
        )
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
    # 风险提示(不改变 ok):如"渠道套了 Cloudflare CDN""探测到间歇性连接失败"
    warnings: list[str] = Field(default_factory=list)


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
    flags = await _cf_flags(rows)
    return [
        _out(r, decrypt(r.api_key), cloudflare=cf)
        for r, cf in zip(rows, flags)
    ]


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
        thinking_mode=_norm_thinking_mode(req.thinking_mode),
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

    cf = await asyncio.to_thread(is_cloudflare_hosted, row.base_url)
    return _out(row, decrypt(row.api_key), cloudflare=cf)


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
    row.thinking_mode = _norm_thinking_mode(req.thinking_mode)
    _apply_default_flags(db, user, row, req)

    db.commit()
    from app.crypto import decrypt

    cf = await asyncio.to_thread(is_cloudflare_hosted, row.base_url)
    return _out(row, decrypt(row.api_key), cloudflare=cf)


class DeleteResult(BaseModel):
    # deleted=True:已删除;False:配置连通正常,需前端二次确认(needs_confirm=True)
    deleted: bool
    needs_confirm: bool = False
    reason: str = ""


async def _config_alive(config_id: int) -> bool:
    """现场探测某配置是否连通(删除前的二次确认判定)。
    任何失败/未配置都视为不连通(可直接删),不抛异常。"""
    try:
        adapter = create_llm_adapter(config_id=config_id, max_tokens=512, timeout=30)
        await adapter.complete(adapter.to_messages("ping"))
        return True
    except EmptyContentError:
        return True  # 空正文说明链路是通的(推理模型没说话而已),仍算"在用"
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
    """用该配置实际调一次模型(设置页的「测试连接」按钮)。

    单次成功只代表"点击那一刻通"。套 CF CDN 的渠道在国内直连下常见分钟级
    间歇故障,正式生成一章要打十几次调用、战线几十分钟,几乎必撞窗口——
    所以对这类渠道在测试通过后再快测两次(间隔 2s),探出链路是否抖动,
    结果放 warnings(不改变 ok,由前端黄条提示)。
    """
    row = _get_row(db, user, config_id)

    from app.crypto import decrypt

    if not decrypt(row.api_key):
        return TestResult(
            ok=False, provider=row.interface_format, error="尚未配置 api_key"
        )

    # max_tokens 给到 1024:100 对推理模型连"思考"都不够,会白白测出空正文
    # 工厂对不支持/配置非法直接抛 ValueError(如类型选成 embedding):这里是
    # 「测试连接」按钮,原样转成 400 文案,不能 500
    try:
        adapter = create_llm_adapter(config_id=config_id, max_tokens=1024, timeout=60)
    except ValueError as exc:
        return TestResult(ok=False, provider=row.interface_format, error=str(exc))
    warnings: list[str] = []
    resp = None
    try:
        resp = await adapter.complete(
            adapter.to_messages("请回复:连接成功")
        )
    except EmptyContentError as exc:
        # 链路是通的(HTTP 打通了、鉴权过了),只是这个模型没把话说出来:
        # 思考吃满输出预算或被安全过滤。判 ok,但把原因挂成警告——正式生成
        # 的长文调用更容易撞上,用户需要提前知道。
        warnings.append(
            f"连接本身正常,但模型没吐出正文:{exc} "
            "(生成时系统会自动放大输出预算重试;若仍频繁失败,"
            "建议换非推理模型或把该配置的 max_tokens 调大)"
        )
    except Exception as exc:  # noqa: BLE001 — 测试接口,错误原样反馈给用户
        return TestResult(
            ok=False, provider=row.interface_format, error=normalize_job_error(exc)[:500]
        )

    if await asyncio.to_thread(is_cloudflare_hosted, row.base_url):
        warnings.append(
            "该渠道套了 Cloudflare CDN,国内网络直连可能出现间歇性连接失败;"
            "生成长文(需多次调用、耗时数十分钟)比单次测试更容易撞上,"
            "若频繁报「上游连续 3 次调用失败」建议换直连渠道"
        )
        flaky = 0
        for _ in range(2):
            await asyncio.sleep(2)
            try:
                await adapter.complete(adapter.to_messages("ping"))
            except EmptyContentError:  # 空正文不算链路问题,上面已单独提示
                pass
            except Exception:  # noqa: BLE001 — 探测失败只计数,不影响 ok
                flaky += 1
        if flaky:
            warnings.append(
                f"稳定性探测:3 次探测中 {1 + flaky} 次失败,该渠道链路当前不稳,"
                "长任务大概率中途断掉,建议暂不用它跑正式生成"
            )
    return TestResult(
        ok=True,
        provider=row.interface_format,
        model=resp.model if resp else row.model,
        reply=(resp.content[:200] if resp else ""),
        warnings=warnings,
    )
