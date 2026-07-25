# app/api/system.py
# -*- coding: utf-8 -*-
"""系统类接口:健康检查 + LLM 冒烟测试。

阶段 0 验收接口:
- GET  /api/health    查看服务与各 provider 配置状态
- POST /api/ping-llm  发一个 prompt，验证能否调通大模型
"""
from __future__ import annotations

import os
import re
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.llm.factory import (
    available_providers,
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)
from app.schemas.system import (
    HealthResponse,
    PingLLMRequest,
    PingLLMResponse,
)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """服务健康检查,并报告各 provider 是否已配置 key。"""
    return HealthResponse(status="ok", providers=available_providers())


@router.get("/mode", include_in_schema=False)
async def mode() -> dict:
    """运行模式:前端据此决定是否跳过登录页。

    公开接口(登录前就要查)。local=桌面单机版免登录;server=多用户需登录。
    """
    from app.config import get_settings

    local = get_settings().is_local
    return {"mode": "local" if local else "server", "is_local": local}


class OpenLinkRequest(BaseModel):
    url: str


@router.post("/system/open-link", include_in_schema=False)
async def open_link(req: OpenLinkRequest) -> dict:
    """用系统默认浏览器打开一个链接——仅 local(桌面单机)模式可用。

    桌面版的 WebView2 窗口不处理 target=_blank 新窗口请求,前端点外链会没反应;
    改由本机后端调 webbrowser.open 兜底。公网 server 模式拒绝(不该替用户开任意链接)。
    """
    from app.config import get_settings

    if not get_settings().is_local:
        raise HTTPException(status_code=403, detail="open-link 仅桌面单机模式可用")

    scheme = urlparse(req.url).scheme
    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持 http/https 链接")

    webbrowser.open(req.url)
    return {"ok": True}


@router.post(
    "/ping-llm",
    response_model=PingLLMResponse,
    # 端点级鉴权:/health 保持公开,ping-llm 要求登录
    # (登录后走当前用户自己配置的 key,不再白嫖服务端 .env 的 key)
    dependencies=[Depends(get_current_user)],
)
async def ping_llm(req: PingLLMRequest) -> PingLLMResponse:
    """给模型发一个 prompt，拿回复。"""
    provider = (req.provider or resolve_default_provider()).lower()

    if not resolve_provider_config(provider)["api_key"]:
        raise HTTPException(
            status_code=400,
            detail=f"provider '{provider}' 尚未配置 api_key,请到设置页填写。",
        )

    adapter = create_llm_adapter(provider)
    try:
        resp = await adapter.complete(adapter.to_messages(req.prompt))
    except Exception as exc:  # noqa: BLE001 — 冒烟接口,直接把错误暴露给调用方
        raise HTTPException(status_code=502, detail=f"调用模型失败: {exc}") from exc

    return PingLLMResponse(
        provider=provider,
        model=resp.model,
        reply=resp.content,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
    )


# CHANGELOG.md 在仓库根目录;容器里 Dockerfile 把它拷到 /srv(即本文件的 parents[3])。
# 开发态 parents[3] 就是仓库根,同样命中。找不到则返回空,不报错。
_CHANGELOG_PATH = Path(__file__).resolve().parents[3] / "CHANGELOG.md"


def _latest_changelog(path: Path = _CHANGELOG_PATH) -> dict:
    """解析 CHANGELOG.md 最新(第一个)一条 "## " 段,返回 {title, body}。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"title": "", "body": ""}
    # 按行首 "## " 切段;第 0 段是 "# 更新日志" 文件头,第 1 段才是最新一条
    sections = re.split(r"(?m)^##\s+", text)
    if len(sections) < 2:
        return {"title": "", "body": ""}
    lines = sections[1].strip().splitlines()
    title = lines[0].strip() if lines else ""
    body = "\n".join(lines[1:]).strip()
    return {"title": title, "body": body}


@router.get("/version", include_in_schema=False)
async def version() -> dict:
    """前端更新提醒用:返回当前部署的 git commit 与最新一条更新日志。

    commit 由构建时 --build-arg GIT_COMMIT 烤进环境变量 APP_COMMIT;本地开发
    没烤则为 "dev",前端据此跳过提示。公开接口(不含敏感信息),登录前也能查。
    """
    return {
        "commit": os.environ.get("APP_COMMIT", "dev"),
        "changelog": _latest_changelog(),
    }
