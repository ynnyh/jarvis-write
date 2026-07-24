# app/config.py
# -*- coding: utf-8 -*-
"""
配置管理：从环境变量 / .env 读取。
借鉴 AI_NovelGenerator 的 config 分组思路：每个 provider 一组，另有任务级模型路由。
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 生产必须用环境变量覆盖的弱默认 JWT 密钥;启动自检据此 fail-fast(见 main.py)。
DEFAULT_JWT_SECRET = "change-me-in-production-please-use-a-random-secret"


class ProviderConfig:
    """单个 LLM provider 的配置载体（运行时从 Settings 组装）。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def is_ready(self) -> bool:
        return bool(self.api_key)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Gemini
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_model: str = "gemini-2.0-flash"

    default_provider: Literal["deepseek", "openai", "gemini"] = "deepseek"

    database_url: str = "sqlite:///./jarvis_write.db"

    # 请求默认参数(max_tokens 取 8192:推理类模型思考会占用输出配额)
    default_temperature: float = 0.7
    default_max_tokens: int = 8192
    default_timeout: int = 600

    # ===== 多用户认证(阶段 8) =====
    # 运行环境:dev(默认,本地开发/测试放行弱默认密钥)/ prod(生产,弱默认即拒启动)。
    # 生产由 docker-compose 设 APP_ENV=prod;启动自检见 main.py。
    app_env: str = "dev"
    # 敏感接口(登录/注册)按 IP 限流的开关:生产默认开,测试关(见 tests/conftest.py)
    rate_limit_enabled: bool = True
    # 注册邀请码:固定共享码,只有填对才能注册(留空则关闭注册,任何人都不能注册)
    invite_code: str = ""
    # JWT 签名密钥:生产务必用环境变量覆盖成随机长串,否则 token 可被伪造
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expire_days: int = 30
    # 初始管理员:首次启动/迁移时自动创建,存量数据归其名下
    admin_username: str = "admin"
    admin_password: str = "admin12345"  # 首次登录后请在设置页修改

    def provider(self, name: str) -> ProviderConfig:
        name = name.lower()
        if name == "deepseek":
            return ProviderConfig(
                self.deepseek_api_key, self.deepseek_base_url, self.deepseek_model
            )
        if name == "openai":
            return ProviderConfig(
                self.openai_api_key, self.openai_base_url, self.openai_model
            )
        if name == "gemini":
            return ProviderConfig(
                self.gemini_api_key, self.gemini_base_url, self.gemini_model
            )
        raise ValueError(f"未知的 provider: {name}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
