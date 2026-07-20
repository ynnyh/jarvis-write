# app/main.py
# -*- coding: utf-8 -*-
"""FastAPI 入口。

阶段 0:
- 启动时建表(SQLite,零配置先跑通)
- 挂载系统路由(/api/health, /api/ping-llm)
- 允许本地前端跨域

运行:  python -m app   或   uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Windows 控制台默认 GBK,强制 stdout/stderr 用 UTF-8,避免中文日志乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.chapters import router as chapters_router
from app.api.consistency import router as consistency_router
from app.api.inspire import router as inspire_router
from app.api.misc import router as misc_router
from app.api.outline import router as outline_router
from app.api.polish import router as polish_router
from app.api.projects import router as projects_router
from app.api.settings import router as settings_router
from app.api.system import router as system_router
from app.api.tendency import router as tendency_router
from app.config import get_settings
from app.db.base import Base
from app.db.session import engine

# 导入 models 触发表注册(SQLAlchemy 需要模型被 import 才会建表)
import app.db.models  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jarvis-write")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表 + 幂等迁移。

    不用 Alembic:create_all 建缺失的表,migrate.py 负责给旧表补列、
    建初始 admin、把存量无主数据归到 admin(全部幂等,每次启动都跑)。
    """
    logger.info("建表中(SQLite)...")
    Base.metadata.create_all(bind=engine)
    logger.info("建表完成,运行多用户迁移...")
    from app.migrate import run_migrations
    run_migrations()
    logger.info("服务就绪。")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="jarvis-write",
        description="AI 长篇小说生成系统 — 重心:长程一致性 / 大纲级联 / 可控倾向",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(projects_router)
    app.include_router(tendency_router)
    app.include_router(settings_router)
    app.include_router(chapters_router)
    app.include_router(consistency_router)
    app.include_router(outline_router)
    app.include_router(polish_router)
    app.include_router(inspire_router)
    app.include_router(misc_router)

    _static_dir = Path(__file__).resolve().parent / "static"
    # 前端构建产物(frontend/dist)挂在 /app
    _frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @app.get("/settings", include_in_schema=False)
    async def settings_page() -> FileResponse:
        return FileResponse(_static_dir / "settings.html")

    if _frontend_dist.exists():
        app.mount(
            "/app",
            StaticFiles(directory=_frontend_dist, html=True),
            name="frontend",
        )

        @app.get("/", include_in_schema=False)
        async def index_redirect() -> RedirectResponse:
            return RedirectResponse(url="/app/")

    @app.get("/api/info", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": "jarvis-write",
            "version": "0.1.0",
            "docs": "/docs",
            "default_provider": settings.default_provider,
        }

    return app


app = create_app()
