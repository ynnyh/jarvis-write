# app/chapter_versions.py
# -*- coding: utf-8 -*-
"""章节正文版本快照:覆盖前留痕,支撑新旧对比与回滚。

设计与 OutlineVersion 对称,但正文体量大,单独成表(chapter_versions)。
核心约定:**任何会覆盖 chapters.final_content 的写入点,覆盖前先调
snapshot_chapter()**。这样被顶替的那一版永远留得下、回得去。

写入点(三处)当前:
  - 章节重生成  app/engines/pipeline/chapter.py(source="generated")
  - 整章润色应用 app/api/polish.py            (source="polished")
  - 手动编辑正文 app/api/chapters.py           (source="edited")
  - 回滚         app/api/chapters.py           (source="restored")
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterVersion


def next_version_number(db: Session, chapter_id: int) -> int:
    """该章下一个快照版本号(从 1 起,连续递增)。"""
    current = (
        db.query(func.max(ChapterVersion.version))
        .filter(ChapterVersion.chapter_id == chapter_id)
        .scalar()
    )
    return (current or 0) + 1


def snapshot_chapter(
    db: Session, chapter: Chapter, source: str
) -> ChapterVersion | None:
    """把 chapter 的**当前**正文存成一版快照(覆盖前调用)。

    空章(final_content 与 draft_content 皆空)不留痕,返回 None——
    没有内容可回退,存空版只会污染历史。不 commit,由调用方随本次事务提交。
    """
    if not (chapter.final_content or chapter.draft_content):
        return None
    snap = ChapterVersion(
        chapter_id=chapter.id,
        version=next_version_number(db, chapter.id),
        draft_content=chapter.draft_content or "",
        final_content=chapter.final_content or "",
        word_count=chapter.word_count or len(chapter.final_content or ""),
        source=source,
    )
    db.add(snap)
    db.flush()
    return snap
