# app/llm/factory.py
# -*- coding: utf-8 -*-
"""
LLM 适配器工厂。

按 interface_format / provider 名称造出对应适配器；
若不传显式参数，则从全局 Settings 读取该 provider 的配置。

用法:
    adapter = create_llm_adapter("deepseek")
    text = await adapter.ask("你好")
"""
from __future__ import annotations

from app.config import get_settings

from .base import LLMAdapter
from .deepseek import DeepSeekAdapter
from .openai import OpenAIAdapter
from .gemini import GeminiAdapter

# interface_format -> 适配器类
_REGISTRY: dict[str, type[LLMAdapter]] = {
    "deepseek": DeepSeekAdapter,
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
}


def _db_settings() -> dict[str, dict]:
    """读数据库里的 provider 配置(设置页保存的)。

    返回 {provider: {api_key, base_url, model, is_default}};读失败返回空
    (建表前/迁移中也能工作,回落到 .env)。
    """
    try:
        from app.db.models import ProviderSetting
        from app.db.session import session_scope

        with session_scope() as db:
            rows = db.query(ProviderSetting).all()
            return {
                r.provider: {
                    "api_key": r.api_key,
                    "base_url": r.base_url,
                    "model": r.model,
                    "is_default": r.is_default,
                }
                for r in rows
            }
    except Exception:  # noqa: BLE001 — 任何读库失败都回落 .env
        return {}


def resolve_provider_config(provider: str) -> dict:
    """合并配置:数据库(设置页)优先,空字段回落 .env。"""
    settings = get_settings()
    env_cfg = settings.provider(provider)
    db_cfg = _db_settings().get(provider, {})
    return {
        "api_key": db_cfg.get("api_key") or env_cfg.api_key,
        "base_url": db_cfg.get("base_url") or env_cfg.base_url,
        "model": db_cfg.get("model") or env_cfg.model,
    }


def resolve_default_provider() -> str:
    """默认 provider:数据库里标了 is_default 的优先,否则用 .env。"""
    for name, cfg in _db_settings().items():
        if cfg.get("is_default") and cfg.get("api_key"):
            return name
    return get_settings().default_provider


def create_llm_adapter(
    provider: str | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> LLMAdapter:
    """造一个适配器。

    provider 缺省用 Settings.default_provider；
    其余参数缺省从该 provider 的 Settings 配置回填。
    """
    settings = get_settings()
    provider = (provider or resolve_default_provider()).lower()

    if provider not in _REGISTRY:
        raise ValueError(
            f"未知 provider: {provider}，可选: {list(_REGISTRY)}"
        )

    cfg = resolve_provider_config(provider)
    adapter_cls = _REGISTRY[provider]

    return adapter_cls(
        api_key=api_key if api_key is not None else cfg["api_key"],
        base_url=base_url if base_url is not None else cfg["base_url"],
        model_name=model_name if model_name is not None else cfg["model"],
        temperature=(
            temperature if temperature is not None else settings.default_temperature
        ),
        max_tokens=(
            max_tokens if max_tokens is not None else settings.default_max_tokens
        ),
        timeout=timeout if timeout is not None else settings.default_timeout,
    )


def available_providers() -> dict[str, bool]:
    """返回各 provider 是否已配置好 key（数据库或 .env 任一即可）。"""
    return {
        name: bool(resolve_provider_config(name)["api_key"]) for name in _REGISTRY
    }
