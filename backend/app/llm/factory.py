# app/llm/factory.py
# -*- coding: utf-8 -*-
"""
LLM 适配器工厂。

cc-switch 风格的多配置体系:每用户可在设置页存多套**命名配置**
(provider_configs 表,如「DeepSeek 官方」「中转站 A」),并各选一个
默认(quality 档)与快档(fast);生成时按任务档位取配置造适配器。

解析优先级:数据库里当前用户的配置 > .env / 环境变量(开发兜底)。

用法:
    adapter = create_llm_adapter()                 # 默认(quality 档)配置
    adapter = create_llm_adapter(config_id=3)      # 指定某套配置
    adapter = create_llm_adapter("deepseek")       # 旧用法:按协议名,DB 优先回落 .env
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.config import get_settings

from .base import LLMAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .deepseek import DeepSeekAdapter
from .openai import OpenAIAdapter
from .gemini import GeminiAdapter
from .anthropic import AnthropicAdapter

logger = logging.getLogger("jarvis-write.llm")

# interface_format -> 适配器类。三个 wire 协议大类 + 两个带预填的别名:
# - openai-compatible:主力通用卡(OpenAI/DeepSeek/Kimi/通义/中转站/本地 Ollama…);
# - anthropic / gemini:各自的原生协议;
# - deepseek / openai:openai-compatible 的别名(存量配置沿用,行为完全等同),
#   保留是为了历史数据零迁移与前端快捷预设,不应再引导用户新建。
_REGISTRY: dict[str, type[LLMAdapter]] = {
    "openai-compatible": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GeminiAdapter,
    "deepseek": DeepSeekAdapter,
    "openai": OpenAIAdapter,
}


def _row_to_config(row) -> dict:
    """ORM 行 → 配置 dict(api_key 解密;库里存的是密文,历史明文兼容)。"""
    from app.crypto import decrypt

    return {
        "id": row.id,
        "name": row.name,
        "interface_format": row.interface_format,
        "api_key": decrypt(row.api_key),
        "base_url": row.base_url,
        "model": row.model,
        "timeout": row.timeout or 0,
        "max_tokens": row.max_tokens or 0,
        "thinking_mode": getattr(row, "thinking_mode", "") or "",
        # 迁移前的旧行可能没有限速两列,getattr 兜底(0 = 不限)
        "max_concurrency": getattr(row, "max_concurrency", 0) or 0,
        "rpm": getattr(row, "rpm", 0) or 0,
        "is_default": row.is_default,
        "is_default_fast": row.is_default_fast,
        # 迁移前的旧行可能没有该属性,getattr 兜底
        "is_default_review": getattr(row, "is_default_review", False),
    }


def _db_configs() -> list[dict]:
    """读数据库里当前用户的全部命名配置(设置页保存的)。

    多用户:只读"当前用户"(contextvar)的配置,各账号 key 互不共用。
    未登录上下文(如迁移脚本)取不到用户时返回空,回落到 .env。
    读失败返回空(建表前/迁移中也能工作,回落到 .env)。
    """
    try:
        from app.auth import current_user_id
        from app.db.models import ProviderConfig
        from app.db.session import session_scope

        uid = current_user_id.get()
        if uid is None:
            return []

        with session_scope() as db:
            rows = (
                db.query(ProviderConfig)
                .filter(ProviderConfig.user_id == uid)
                .order_by(ProviderConfig.id)
                .all()
            )
            return [_row_to_config(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — 任何读库失败都回落 .env,但要留痕
        logger.warning("读取数据库模型配置失败,回落 .env: %s", exc)
        return []


def _env_config(provider: str) -> dict:
    """.env 里的 provider 配置包装成与 DB 配置同形的 dict(id=None 标记来源)。"""
    env = get_settings().provider(provider)
    return {
        "id": None,
        "name": f"{provider}(.env)",
        "interface_format": provider,
        "api_key": env.api_key,
        "base_url": env.base_url,
        "model": env.model,
        "timeout": 0,
        "max_tokens": 0,
        "thinking_mode": "",
        "is_default": False,
        "is_default_fast": False,
    }


def resolve_tier_config(tier: str = "quality") -> dict:
    """档位(quality/fast/review) → 当前生效的配置,按优先级回落:

    quality 档:
    ① 数据库里标了 is_default 且有 key 的;② 数据库里任一有 key 的(按创建序);
    ③ .env default_provider 且有 key;④ .env 里任一有 key 的;
    ⑤ 全都没有才返回 .env default_provider——此时 create_llm_adapter
      会抛出 400「未配置 API key」,而不是发一个空 Bearer 请求。

    fast 档:先找标了 is_default_fast 且有 key 的,没有则完全跟随 quality 链
    (快档未单独指定 = 与主档同配置)。

    review 档(主审/一致性门禁/定点修复):先找标了 is_default_review 且有 key
    的,没有则跟随 quality 链——写手与审校分模型是可选项,不设=行为与旧版一致。
    """
    valid = [c for c in _db_configs() if c["api_key"]]
    if tier == "fast":
        for c in valid:
            if c["is_default_fast"]:
                return c
    if tier == "review":
        for c in valid:
            if c["is_default_review"]:
                return c
    for c in valid:
        if c["is_default"]:
            return c
    if valid:
        return valid[0]
    env_default = get_settings().default_provider
    if _env_config(env_default)["api_key"]:
        return _env_config(env_default)
    for name in _REGISTRY:
        if _env_config(name)["api_key"]:
            return _env_config(name)
    return _env_config(env_default)


def get_config_by_id(config_id: int) -> dict:
    """按 id 取当前用户的某套配置;不存在/不属于当前用户 → 400。"""
    for c in _db_configs():
        if c["id"] == config_id:
            return c
    raise HTTPException(status_code=404, detail="模型配置不存在或已被删除。")


def resolve_provider_config(provider: str) -> dict:
    """【旧接口兼容】某协议的 {api_key, base_url, model}。

    数据库里该协议的任一配置优先(**取创建最早的一套**),空字段回落 .env。

    ⚠ 这个「取最早一套」的语义只对「.env 里一个协议一套配置」的老世界成立。
    cc-switch 之后同一协议可以有多套命名配置(几个中转站 + 官方各一套),
    此时它取到的很可能不是用户标了「默认」的那套。所以**不要拿协议名当配置用**:
    要当前生效的配置就走 `resolve_tier_config` / `create_llm_adapter()` 无参形态,
    要指定某一套就传 `config_id`。曾经有个 `resolve_default_provider()`
    (返回默认档的协议名)专门用来喂这个函数,书名/简介/封面/主题曲/投稿包
    这几条线因此永远打到最早那套中转站——用户在设置页换成官方 DeepSeek 也没用,
    报错还是那个中转站的 Cloudflare 挑战页(HTTP 403 "Just a moment...")。
    该函数已删除,别再加回来;它唯一的正当用途(读默认档协议名做断言)用
    `resolve_tier_config("quality")["interface_format"]` 表达。
    """
    settings = get_settings()
    env_cfg = settings.provider(provider)
    db_cfg = next(
        (c for c in _db_configs() if c["interface_format"] == provider), None
    ) or {}
    return {
        "api_key": db_cfg.get("api_key") or env_cfg.api_key,
        "base_url": db_cfg.get("base_url") or env_cfg.base_url,
        "model": db_cfg.get("model") or env_cfg.model,
    }


def create_llm_adapter(
    provider: str | None = None,
    *,
    config_id: int | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    thinking_mode: str | None = None,
    max_concurrency: int | None = None,
    rpm: int | None = None,
) -> LLMAdapter:
    """造一个适配器。

    配置来源三选一:
    - config_id:指定某套命名配置(校验归属当前用户);
    - provider:按协议名,数据库该协议的配置优先、空字段回落 .env(旧用法);
    - 都不传:quality 档当前生效配置(resolve_tier_config)。

    api_key 为空时抛 HTTPException(400)——让 FastAPI 返回清晰错误,
    而不是带着空 Bearer 头发请求(那会变成难以排查的 500)。

    timeout / max_tokens / thinking_mode 覆盖优先级:显式参数 > 配置里的值 >
    全局 default_*。thinking_mode 显式指定(参数或配置列)时 thinking_forced=True,
    不受"模型名像推理系才下发"的启发式限制(照顾被中转站改名的模型)。
    """
    settings = get_settings()

    if config_id is not None:
        cfg = get_config_by_id(config_id)
    elif provider:
        provider = provider.lower()
        if provider not in _REGISTRY:
            raise ValueError(
                f"未知 provider: {provider}，可选: {list(_REGISTRY)}"
            )
        merged = resolve_provider_config(provider)
        db_cfg = next(
            (c for c in _db_configs() if c["interface_format"] == provider), None
        ) or {}
        cfg = {
            **_env_config(provider),
            **merged,
            "interface_format": provider,
            "timeout": db_cfg.get("timeout", 0),
            "max_tokens": db_cfg.get("max_tokens", 0),
            "max_concurrency": db_cfg.get("max_concurrency", 0),
            "rpm": db_cfg.get("rpm", 0),
            "thinking_mode": db_cfg.get("thinking_mode", ""),
        }
    else:
        cfg = resolve_tier_config("quality")

    adapter_cls = _REGISTRY.get(cfg["interface_format"])
    if adapter_cls is None:
        raise ValueError(
            f"未知 provider: {cfg['interface_format']}，可选: {list(_REGISTRY)}"
        )

    resolved_key = api_key if api_key is not None else cfg["api_key"]
    if not (resolved_key or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                f"未配置 {cfg['interface_format']} 的 API key,"
                "请到「模型设置」页配置后再试。"
            ),
        )

    # 思考模式:显式参数 > 配置列("" 视为未指定)> 全局默认(默认 disabled,
    # 见 config.default_thinking_mode 的注释——V4 系思考默认开且 effort=high,
    # 会吃光输出预算)。显式来源才置 forced。
    forced = thinking_mode is not None or bool(cfg.get("thinking_mode"))
    resolved_thinking = (
        thinking_mode
        if thinking_mode is not None
        else (cfg.get("thinking_mode") or settings.default_thinking_mode)
    )

    return adapter_cls(
        api_key=resolved_key,
        base_url=base_url if base_url is not None else cfg["base_url"],
        model_name=model_name if model_name is not None else cfg["model"],
        temperature=(
            temperature if temperature is not None else settings.default_temperature
        ),
        max_tokens=(
            max_tokens
            if max_tokens is not None
            else (cfg.get("max_tokens") or settings.default_max_tokens)
        ),
        timeout=(
            timeout
            if timeout is not None
            else (cfg.get("timeout") or settings.default_timeout)
        ),
        thinking_mode=resolved_thinking,
        thinking_forced=forced,
        # 主动限速:显式参数 > 配置列 > 0(不限)。见 llm/throttle.py
        max_concurrency=(
            max_concurrency
            if max_concurrency is not None
            else (cfg.get("max_concurrency") or 0)
        ),
        rpm=rpm if rpm is not None else (cfg.get("rpm") or 0),
    )


def available_providers() -> dict[str, bool]:
    """返回各协议是否已配置好 key（数据库或 .env 任一即可）。"""
    db_formats = {c["interface_format"] for c in _db_configs() if c["api_key"]}
    return {
        name: (name in db_formats) or bool(_env_config(name)["api_key"])
        for name in _REGISTRY
    }
