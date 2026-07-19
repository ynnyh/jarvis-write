"""API 路由包。

各路由模块在此汇总为一个 router，供 main.py 挂载。
"""
from fastapi import APIRouter

from . import system

api_router = APIRouter()
api_router.include_router(system.router, tags=["system"])
