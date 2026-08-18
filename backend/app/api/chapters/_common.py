# app/api/chapters/_common.py
# -*- coding: utf-8 -*-
"""章节接口子包的公共件:跨端点复用的 schema 与 helper。

拆分自原单文件 app/api/chapters.py(1091 行)。路由/行为零变化:各子模块
(generation/revision/extraction/issues/release/versions/read)只挂自己的端点,
__init__.py 用带 prefix + 鉴权依赖的主 router 聚合。
"""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Chapter
from app.engines.pipeline.handoff import handoff_payload
from app.engines.polish import ai_flavor_report


def _db_locked(exc: BaseException) -> bool:
    """是否 SQLite 写锁冲突(含 WAL 下旧快照升级写锁、不走 busy_timeout 的那种)。"""
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


class ChapterBrief(BaseModel):
    chapter_number: int
    status: str
    word_count: int
    is_stale: bool

    model_config = {"from_attributes": True}


class ChapterDetail(ChapterBrief):
    draft_content: str
    final_content: str
    outline_version_used: int
    # 本章章末交接契约(docs/08 §5.2):无契约/正文已改动(指纹不符)时为 None;
    # status:none(从未提取)/ ok / failed(失败留痕,error 记原因,不阻塞生成)
    handoff_contract: dict | None = None
    handoff_extract_status: str = "none"
    handoff_extract_error: str = ""


def _fill_handoff(db: Session, chapter: Chapter, resp: ChapterDetail) -> None:
    """把本章契约填进响应(契约存在 chapter_states 表,不在 chapters 行上)。"""
    payload = handoff_payload(db, chapter)
    resp.handoff_contract = payload["contract"]
    resp.handoff_extract_status = payload["status"]
    resp.handoff_extract_error = payload["error"]


def _gate_payload(chapter: Chapter, issues: list[dict]) -> dict:
    """门禁结果透出:quarantined 状态 + blocker 列表(P1 前端审核面板对接用)。"""
    return {
        "status": "quarantined" if chapter.status == "quarantined" else "passed",
        "blockers": [i for i in issues if i.get("severity") == "blocker"],
    }


def _flavor_dict(text: str) -> dict:
    """生成结果卡的 AI 味字段:score/summary + 分类得分明细(hover 展示用)。"""
    report = ai_flavor_report(text)
    return {
        "score": report.score,
        "summary": report.summary(),
        "categories": report.categories,
    }


def _get_chapter_or_404(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚未生成")
    return ch
