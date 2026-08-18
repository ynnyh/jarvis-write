# app/api/chapters/__init__.py
# -*- coding: utf-8 -*-
"""章节接口子包:逐章生成、改稿、同步、门禁放行、版本、查看。

拆分自原 chapters.py(仅按职责分文件,路由/行为零变更):
- generation  单章生成(同步/异步)+ 连写队列
- revision    重写研讨、多处批注改、手动编辑正文
- extraction  手改重抽取、契约重提 + 门禁重检
- issues      一致性问题清单、状态流转、按问题修订
- release     人工审核通过、quarantined 放行
- versions    历史版本列表/详情/回退

本文件除聚合上述子 router 外,自持两个「根级读取」端点(章节列表 / 单章详情):
列表端点是空 path,FastAPI 要求它挂在带 prefix 的 router 上(bare 子 router 无
prefix 会在 include 时报 "Prefix and path cannot be both empty"),故直接定义在
主 router;/{chapter_number} 通配随之放最后,不遮蔽任何字面/多段子路由。

前缀 /api/projects/{project_id}/chapters、tags、鉴权依赖都声明在主 router 上,
include_router 时 FastAPI 自动下发到各子路由,整体行为与拆分前一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter
from app.db.session import get_db

from . import extraction, generation, issues, release, revision, versions

# 向后兼容 re-export:外部按 `from app.api.chapters import _flavor_dict` 引用
# (tests/test_ai_flavor.py);列入 __all__ 表明是有意导出。
from ._common import (
    ChapterBrief,
    ChapterDetail,
    _db_locked,
    _fill_handoff,
    _flavor_dict,
    _gate_payload,
    _get_chapter_or_404,
)

router = APIRouter(
    prefix="/api/projects/{project_id}/chapters",
    tags=["chapters"],
    dependencies=[Depends(get_current_user)],
)

# 细分子功能端点(均为 /{chapter_number}/... 多段,或 /generate-queue 字面单段)
router.include_router(generation.router)
router.include_router(revision.router)
router.include_router(extraction.router)
router.include_router(issues.router)
router.include_router(release.router)
router.include_router(versions.router)


# —— 根级读取端点 ——
# 空 path 的 list 必须挂在带 prefix 的 router 上(见模块 docstring);
# /{chapter_number} 通配放在所有子路由之后,不遮蔽上面任何字面/多段路由。
@router.get("", response_model=list[ChapterBrief])
async def list_chapters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return list(
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )


@router.get("/{chapter_number}", response_model=ChapterDetail)
async def get_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")
    resp = ChapterDetail.model_validate(ch, from_attributes=True)
    _fill_handoff(db, ch, resp)
    return resp


__all__ = [
    "router",
    "ChapterBrief",
    "ChapterDetail",
    "_db_locked",
    "_fill_handoff",
    "_flavor_dict",
    "_gate_payload",
    "_get_chapter_or_404",
]
