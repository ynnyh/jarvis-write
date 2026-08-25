# app/db/models/setting.py
"""运行时设置:LLM 模型配置存库,让用户在站点设置页配置,而非改 .env。

阶段 8 起改为**每用户一份**:每个账号进来单独配自己的 key,互不共用。
优先级:数据库里当前用户的配置 > .env / 环境变量(.env 仅作开发兜底)。

cc-switch 风格改造:ProviderSetting(老表,每用户每协议一行)升级为
ProviderConfig(新表 provider_configs)——每用户可存多套**命名配置**
(如「DeepSeek 官方」「中转站 A」),一键切换默认(quality 档)与
快档(fast),并可按配置覆盖 timeout / max_tokens。
老表保留仅作迁移数据源,代码不再读写。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ProviderSetting(Base, TimestampMixin):
    """【已废弃,仅作迁移数据源】某用户的一个 LLM provider 配置。

    数据由 migrate._migrate_provider_settings_to_configs 搬到 provider_configs,
    此后新代码一律用 ProviderConfig。
    """

    __tablename__ = "provider_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_per_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), index=True)
    api_key: Mapped[str] = mapped_column(String(300), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    is_default: Mapped[bool] = mapped_column(default=False)


class ProviderConfig(Base, TimestampMixin):
    """某用户的一套命名 LLM 配置(cc-switch 风格,每用户可多套、一键切换)。

    - interface_format: 协议卡(deepseek/openai/gemini),决定用哪个适配器;
    - is_default: quality 档(架构/定稿/抽取等重活)用的配置,全用户唯一;
    - is_default_fast: fast 档(草稿/摘要/校验)用的配置,全用户唯一,
      未设置时 fast 档回落到 quality 档配置;
    - timeout / max_tokens: 0 = 跟随全局 default_* 与任务级默认;
    - thinking_mode: 思考模式控制,空串 = 跟随全局默认(关思考,
      见 config.default_thinking_mode);low/high/max = 按配置强制指定。
    """

    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50), default="")
    interface_format: Mapped[str] = mapped_column(String(20), index=True)
    api_key: Mapped[str] = mapped_column(String(300), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    timeout: Mapped[int] = mapped_column(Integer, default=0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_mode: Mapped[str] = mapped_column(String(10), default="", server_default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    is_default_fast: Mapped[bool] = mapped_column(default=False)
