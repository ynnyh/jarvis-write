# app/api/search.py
# -*- coding: utf-8 -*-
"""全书全文检索(FTS5 trigram,评价第 4 条)。

GET /api/projects/{id}/search?q=寒字七72
  → 跨五章索引(chapters/outlines/entities/facts/foreshadowings)搜子串,
    返回分组结果 + 就地裁剪的上下文摘要,前端点击章节命中直达该章。

实现约定(与迁移 0008 成对,改一处必改另一处):
  · 虚表 fts_* 的 rowid = 源表 id,content 列即被索引文本;
  · ≥3 字走 MATCH 倒排(bm25 排序);<3 字降级为对 content 列 LIKE
    (trigram 对 <3 字面量无法走倒排;单书数据量全扫可接受);
  · 摘要在 Python 里裁剪——SQL snippet() 只支持 MATCH 路径,LIKE 路径会报错,
    统一走 Python 保证两条路径行为一致。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.session import get_db

router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["search"],
    dependencies=[Depends(get_current_user)],
)

# 每类命中最多返回条数(前端分组展示,全书爆量时截断即可)
PER_KIND_LIMIT = 20

# (类型, 虚表, 元数据列, 元数据里代表「章号」的列)——顺序即结果分组展示顺序
_KINDS: list[tuple[str, str, str, str | None]] = [
    ("chapter", "fts_chapters", "chapter_number", "chapter_number"),
    ("outline", "fts_outlines", "chapter_number, title", "chapter_number"),
    ("entity", "fts_entities", "name, entity_type", None),
    ("fact", "fts_facts", "source_chapter", "source_chapter"),
    ("foreshadowing", "fts_foreshadowings", "chapter_planted, status", "chapter_planted"),
]

_KIND_CN = {
    "chapter": "正文",
    "outline": "大纲",
    "entity": "设定",
    "fact": "事实",
    "foreshadowing": "伏笔",
}


class SearchHit(BaseModel):
    kind: str
    kind_cn: str
    ref_id: int
    # 定位信息:章命中=章号;大纲=章号+标题;实体=名称+类型;其余为章号(可空)
    chapter_number: int | None = None
    title: str | None = None
    name: str | None = None
    snippet: str
    hits: int  # 该条内容里查询词出现次数


class SearchResponse(BaseModel):
    q: str
    total: int
    elapsed_ms: int
    grouped: dict[str, list[SearchHit]]  # kind → hits


def _snippet(content: str, q: str, width: int = 46) -> tuple[str, int]:
    """就地裁剪第一个命中处前后的上下文;统计总命中次数。"""
    hits = content.count(q)
    idx = content.find(q)
    if idx < 0:
        return (content[:width] + "…") if len(content) > width else content, 0
    start = max(0, idx - width // 3)
    end = min(len(content), idx + len(q) + width)
    frag = content[start:end].replace("\n", " ").replace("\r", "")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{frag}{suffix}", hits


def _match_expr(q: str) -> str:
    """FTS5 MATCH 短语:整句包引号防语法符;内部引号双写转义。"""
    return '"' + q.replace('"', '""') + '"'


@router.get("/search", response_model=SearchResponse)
def search(
    project_id: int,
    q: str = Query(min_length=1, max_length=200, description="查询串(子串匹配)"),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    t0 = time.perf_counter()
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="查询串不能为空")

    use_match = len(q) >= 3
    grouped: dict[str, list[SearchHit]] = {}
    total = 0

    for kind, table, meta_cols, ch_col in _KINDS:
        if use_match:
            sql = text(
                f"SELECT rowid AS ref_id, content, project_id, {meta_cols}, "
                f"bm25({table}) AS rank FROM {table} "
                f"WHERE {table} MATCH :m AND project_id = :pid "
                f"ORDER BY rank LIMIT :lim"
            )
            params: dict = {"m": _match_expr(q), "pid": project_id, "lim": PER_KIND_LIMIT}
        else:
            sql = text(
                f"SELECT rowid AS ref_id, content, project_id, {meta_cols} "
                f"FROM {table} WHERE project_id = :pid AND content LIKE :like "
                f"LIMIT :lim"
            )
            params = {"pid": project_id, "like": f"%{q}%", "lim": PER_KIND_LIMIT}
        rows = db.execute(sql, params).mappings().all()

        hits: list[SearchHit] = []
        for r in rows:
            frag, n_hits = _snippet(r["content"] or "", q)
            if n_hits == 0:
                continue  # bm25 偶发的弱相关行,只留真实命中的
            hit = SearchHit(
                kind=kind, kind_cn=_KIND_CN[kind], ref_id=r["ref_id"],
                chapter_number=r.get(ch_col) if ch_col else None,
                title=r.get("title"),
                name=r.get("name"),
                snippet=frag, hits=n_hits,
            )
            hits.append(hit)
        hits.sort(key=lambda h: -h.hits)
        grouped[kind] = hits
        total += len(hits)

    return SearchResponse(q=q, total=total, elapsed_ms=int((time.perf_counter() - t0) * 1000), grouped=grouped)
