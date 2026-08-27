# app/api/drama/__init__.py
# -*- coding: utf-8 -*-
"""漫剧工坊 API 主路由。

拆分自原 app/api/drama.py(42KB 单文件)。按子资源拆分为:
- style.py      美术风格卡
- characters.py  角色卡 / 场景卡 / 定妆照 / 声线选型
- episodes.py    集规划 / 列表 / 详情 / 删除 / 剧本 / 分镜 / 提示词 / 成片包
- shots.py       分镜手动编辑 / 逐格挂素材
- trailer.py     预告片
- export.py      集导出
- _common.py     共享请求模型 / 工具函数 / imports

对外接口不变:from app.api.drama import router as drama_router
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.drama._common import (
    DIRECTIONS,
    MODE_DESC,
    approved_chapter_numbers,
    get_current_user,
    get_db,
    get_project_or_404,
    make_sub_router,
    ROUTER_PREFIX,
)
from app.api.drama.characters import router as characters_router
from app.api.drama.episodes import router as episodes_router
from app.api.drama.export import router as export_router
from app.api.drama.shots import router as shots_router
from app.api.drama.style import router as style_router
from app.api.drama.trailer import router as trailer_router

# 主路由:定义前缀和鉴权依赖,子路由通过 include_router 自动继承
router = APIRouter(
    prefix=ROUTER_PREFIX,
    tags=["drama"],
    dependencies=[Depends(get_current_user)],
)


# =============== 准入信息 ===============


@router.get("/meta")
async def drama_meta(project_id: int, db: Session = Depends(get_db)):
    """准入门槛数据:已定稿章号(前端用它显示引导/章节范围选择)+ 画风方向目录。"""
    get_project_or_404(db, project_id)
    approved = approved_chapter_numbers(db, project_id)
    return {
        "approved_chapters": approved,
        "approved_count": len(approved),
        "modes": [{"key": k, "label": v} for k, v in MODE_DESC.items()],
        "directions": DIRECTIONS,
    }


# 聚合所有子路由(路径前缀和鉴权依赖已在各子模块的 make_sub_router 里定义)
router.include_router(style_router)
router.include_router(characters_router)
router.include_router(episodes_router)
router.include_router(shots_router)
router.include_router(trailer_router)
router.include_router(export_router)
