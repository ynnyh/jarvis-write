# backend/scripts/smoke_test.py
# -*- coding: utf-8 -*-
"""阶段 0 冒烟测试:不起 HTTP 服务，直接在进程内验证核心装配。

验证项:
  1. app 能正常导入(所有模块无循环依赖/命名错误)
  2. 建表成功,且包含设计文档里的关键表
  3. 路由已注册(/api/health, /api/ping-llm)
  4. LLM 工厂能按 provider 造出适配器
  5. 各 provider 配置就绪状态可查询

用法:
    cd backend
    .venv/Scripts/python -m scripts.smoke_test
退出码 0 = 全部通过;非 0 = 有失败项。
"""
from __future__ import annotations

import sys

# 关键表(来自 docs/02-data-model.md),缺一不可
EXPECTED_TABLES = {
    "projects",
    "architecture",
    "outlines",
    "outline_versions",
    "chapters",
    "entities",
    "facts",
    "relationships",
    "knowledge_states",
    "foreshadowings",
    "tendency_presets",
}

EXPECTED_ROUTES = {"/api/health", "/api/ping-llm"}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    results: list[bool] = []

    # 1. 导入 app
    try:
        from app.main import app

        results.append(_check("import app", True))
    except Exception as e:  # noqa: BLE001
        _check("import app", False, repr(e))
        return 1  # 后续都依赖它,直接退出

    # 2. 建表并核对
    try:
        from app.db.base import Base
        from app.db.session import engine

        Base.metadata.create_all(bind=engine)
        tables = set(Base.metadata.tables.keys())
        missing = EXPECTED_TABLES - tables
        results.append(
            _check(
                "database tables",
                not missing,
                f"共 {len(tables)} 张表" if not missing else f"缺失: {missing}",
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(_check("database tables", False, repr(e)))

    # 3. 路由注册
    try:
        routes = {getattr(r, "path", None) for r in app.routes}
        missing_routes = EXPECTED_ROUTES - routes
        results.append(
            _check(
                "routes registered",
                not missing_routes,
                "已注册" if not missing_routes else f"缺失: {missing_routes}",
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(_check("routes registered", False, repr(e)))

    # 4. LLM 工厂
    try:
        from app.llm.factory import create_llm_adapter

        adapter = create_llm_adapter("deepseek")
        results.append(
            _check(
                "llm factory",
                adapter is not None and hasattr(adapter, "ask"),
                f"deepseek -> {type(adapter).__name__}",
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(_check("llm factory", False, repr(e)))

    # 5. provider 就绪状态可查
    try:
        from app.llm.factory import available_providers

        providers = available_providers()
        results.append(
            _check(
                "provider status",
                set(providers) == {"deepseek", "openai", "gemini"},
                str(providers),
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(_check("provider status", False, repr(e)))

    print("-" * 48)
    passed = sum(results)
    total = len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
