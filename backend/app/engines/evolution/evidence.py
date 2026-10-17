# app/engines/evolution/evidence.py
# -*- coding: utf-8 -*-
"""复盘证据包:把「作者写了什么、改了什么、说过什么」收进一个有预算的结构(docs/36 §4)。

三条证据来源,可信度递减:
1. 作者采纳的正文(approved 章 final_content)——满意信号的正面样本;
2. 作者动手的痕迹(draft → final 的主要删改)——不满意信号,教训的最硬证据,
   只用于提炼教训,绝不把被删的草稿当正面样本;
3. 作者下过的指令(GenInput 里的用户输入:点子/补充说明/重写要求)。

所有块都有字数预算:复盘是一次审校档调用,prompt 膨胀就是烧钱 + 稀释证据。
本模块只读库、只产聚合与节选,不含任何写操作。
"""
from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chapter, GenInput, Project

# 正面样本:首/中/尾各取一章(整本的趋势比某一章的精彩更重要)
_SAMPLE_CHAPTERS = 3
_SAMPLE_CHARS = 2500
# 删改痕迹:只看最近几章(作者越写越顺手,早期磨合期的噪音大)
_EDIT_CHAPTERS = 2
_EDIT_SPAN_MIN = 8  # 比这还短的删改多为标点微调,是噪音
_EDIT_SPANS = 15
_EDIT_SPAN_CHARS = 200
_EDIT_TOTAL_CHARS = 3000
# 指令摘录:只取与创作意图相关的环节,新→旧
_DIRECTIVE_KINDS = (
    "chapter-revision", "revise-plan", "intent-readback",
    "book-plans", "intent-confirm",
)
_DIRECTIVE_ROWS = 30
_DIRECTIVE_VALUE_CHARS = 400
_DIRECTIVE_TOTAL_CHARS = 6000


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap] + "…(截断)"


def _extract_edit_spans(draft: str, final: str, chapter: int) -> dict[str, Any] | None:
    """草稿 → 定稿的主要删改片段。只挑够长的块,按长度取最有分量的几段。"""
    if not draft or not final or draft == final:
        return None
    spans: list[str] = []
    total = 0
    raw: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, draft, final).get_opcodes():
        if op == "equal":
            continue
        if op in ("delete", "replace"):
            piece = draft[i1:i2]
        else:  # insert:作者亲手加的,也是痕迹
            piece = final[j1:j2]
        piece = piece.strip()
        if len(piece) < _EDIT_SPAN_MIN:
            continue
        raw.append(piece)
    raw.sort(key=len, reverse=True)
    for piece in raw[:_EDIT_SPANS]:
        clipped = _clip(piece, _EDIT_SPAN_CHARS)
        spans.append(clipped)
        total += len(clipped)
        if total >= _EDIT_TOTAL_CHARS:
            break
    if not spans:
        return None
    return {"chapter": chapter, "spans": spans}


def gather_project_evidence(db: Session, project: Project) -> dict[str, Any]:
    """汇总一次复盘需要的全部证据。纯只读;字段缺失一律回落空值,不抛。"""
    approved = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.status == "approved",
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    total = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .count()
    )
    words = sum(int(c.word_count or 0) for c in approved)

    # 正面样本:首/中/尾
    picks: list[Chapter] = []
    if approved:
        idx = {0, len(approved) // 2, len(approved) - 1}
        picks = [approved[i] for i in sorted(idx)]
    samples = [
        {"chapter": c.chapter_number, "text": _clip(c.final_content or "", _SAMPLE_CHARS)}
        for c in picks
    ]

    # 删改痕迹:最近几章里草稿与定稿分叉最大的
    edits: list[dict[str, Any]] = []
    for c in reversed(approved):
        if len(edits) >= _EDIT_CHAPTERS:
            break
        span = _extract_edit_spans(c.draft_content or "", c.final_content or "", c.chapter_number)
        if span:
            edits.append(span)

    # 作者指令:新→旧,只收字符串值(卡/候选那些 dict 噪音不要)
    directives: list[str] = []
    used = 0
    rows = (
        db.query(GenInput)
        .filter(GenInput.project_id == project.id, GenInput.kind.in_(_DIRECTIVE_KINDS))
        .order_by(GenInput.id.desc())
        .limit(_DIRECTIVE_ROWS)
        .all()
    )
    for row in rows:
        pieces = [
            f"{key}:{_clip(str(v).strip(), _DIRECTIVE_VALUE_CHARS)}"
            for key, v in (row.payload or {}).items()
            if isinstance(v, str) and v.strip()
        ]
        if not pieces:
            continue
        line = " | ".join(pieces)
        if used + len(line) > _DIRECTIVE_TOTAL_CHARS:
            break
        directives.append(f"[{row.kind}] {_clip(line, _DIRECTIVE_VALUE_CHARS * 2)}")
        used += len(line)

    return {
        "meta": {
            "title": project.title or "(未命名)",
            "genre": project.genre or "(未定)",
            "mode": project.mode or "serial",
            "finished": bool(project.finished),
        },
        "chapter_stats": {"total": total, "approved": len(approved), "words": words},
        "samples": samples,
        "edits": edits,
        "directives": directives,
    }
