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
from app.api.edit_directive import router as edit_directive_router
from app.api.editorial import router as editorial_router
from app.api.inspire import router as inspire_router
from app.api.media import router as media_router
from app.api.misc import router as misc_router
from app.api.outline import router as outline_router
from app.api.overview import router as overview_router
from app.api.polish import router as polish_router
from app.api.projects import router as projects_router
from app.api.settings import router as settings_router
from app.api.submission import router as submission_router
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


def _assert_secure_config() -> None:
    """生产环境(APP_ENV=prod)拒绝以弱默认 JWT 密钥启动。

    弱 jwt_secret 可被任何人用来伪造任意 user_id 的 JWT → 接管账号、读所有人的
    小说与 per-user key。docker-compose 已用 ${JWT_SECRET:?} 强制,此处是「不走
    compose、裸 uvicorn/docker run 起服务」时的兜底。dev 放行,不打扰本地开发/测试。
    """
    from app.config import DEFAULT_JWT_SECRET, get_settings

    settings = get_settings()
    if settings.app_env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET 仍是弱默认值,拒绝在非 dev 环境启动:请用环境变量设一个随机长串"
            "(否则任何人都能伪造 JWT 接管账号)。见 docs/06-改造方案。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表 + 幂等迁移。

    不用 Alembic:create_all 建缺失的表,migrate.py 负责给旧表补列、
    建初始 admin、把存量无主数据归到 admin(全部幂等,每次启动都跑)。
    """
    _assert_secure_config()  # 生产弱密钥即拒启动(见函数注释)
    logger.info("建表中(SQLite)...")
    Base.metadata.create_all(bind=engine)
    logger.info("建表完成,运行多用户迁移...")
    from app.migrate import run_migrations
    run_migrations()
    from app.jobs import cleanup_stuck_jobs
    cleanup_stuck_jobs()
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

    # local(桌面)模式:前端由 Tauri 壳内嵌或后端自托管,放行 tauri 与本机源;
    # server 模式:只放行本地开发前端(生产同源,无需 CORS)。
    _cors_origins = (
        [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "tauri://localhost",
            "https://tauri.localhost",
        ]
        if settings.is_local
        else ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 登录/注册按 IP 限流,挡撞库 / 批量刷号(单进程内存计数,见 ratelimit.py)。
    # local 模式免登录,无登录接口可刷,限流无意义,直接关。
    if settings.rate_limit_enabled and not settings.is_local:
        from app.ratelimit import RateLimitMiddleware
        app.add_middleware(RateLimitMiddleware)

    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(projects_router)
    app.include_router(tendency_router)
    app.include_router(settings_router)
    app.include_router(chapters_router)
    app.include_router(consistency_router)
    app.include_router(outline_router)
    app.include_router(overview_router)
    app.include_router(edit_directive_router)
    app.include_router(editorial_router)
    app.include_router(polish_router)
    app.include_router(inspire_router)
    app.include_router(submission_router)
    app.include_router(media_router)
    app.include_router(misc_router)

    # 资源定位统一走 resource_path:源码环境相对 backend/,冻结(桌面版)相对
    # sys._MEIPASS。打包 spec 把 app/static 与 frontend/dist 放到对应相对路径,
    # 两种环境同一套代码即可命中。
    from app.paths import resource_path

    _static_dir = resource_path("app/static")
    # 前端构建产物(frontend/dist)挂在 /app
    _frontend_dist = resource_path("frontend/dist")

    @app.get("/settings", include_in_schema=False)
    async def settings_page() -> FileResponse:
        return FileResponse(_static_dir / "settings.html")

    if _frontend_dist.exists():
        # index.html 强制不缓存:否则浏览器缓存了旧 index,会一直引用旧哈希的
        # JS/CSS,用户看不到更新。带哈希的 assets 可放心长缓存(文件名变即失效)。
        class _NoCacheHTMLStatic(StaticFiles):
            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                if path.endswith(".html") or path in ("", "."):
                    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
                return resp

        app.mount(
            "/app",
            _NoCacheHTMLStatic(directory=_frontend_dist, html=True),
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
