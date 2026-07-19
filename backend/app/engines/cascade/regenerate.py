# app/engines/cascade/regenerate.py
# -*- coding: utf-8 -*-
"""级联重生成:用户确认后,按章节顺序重写受影响章节的大纲。

原则:最小侵入(能保留的保留)、伏笔链完整、只动用户勾选的章。
重生成后:升版本存快照;该章已有正文 → is_stale=true。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, Outline, OutlineVersion, Project
from app.engines.cascade.differ import outline_to_dict, _fmt
from app.engines.cascade.impact import _latest_change_summary
from app.engines.pipeline.blueprint import _outline_content_hash
from app.engines.pipeline.blueprint_parser import parse_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import OUTLINE_REGENERATE_PROMPT
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.cascade")


def _architecture_brief(project: Project) -> str:
    arch = project.architecture
    if arch is None:
        return "(无)"
    return f"核心种子:{arch.core_seed}\n情节架构(节选):{arch.plot_architecture[:800]}"


def _get_outline(db: Session, project_id: int, n: int) -> Outline | None:
    return (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )


async def cascade_regenerate(
    db: Session,
    project: Project,
    source_chapter: int,
    chapter_numbers: list[int],
    reasons: dict[int, str] | None = None,
    tendency: Tendency | None = None,
) -> dict:
    """重生成指定章节的大纲。返回 {updated: [...], stale_chapters: [...], warnings: [...]}。"""
    source = _get_outline(db, project.id, source_chapter)
    if source is None:
        raise ValueError(f"源章节 {source_chapter} 大纲不存在")

    reasons = reasons or {}
    assembled = assemble_tendency("outline", tendency, project.global_tendency)
    style_block = render_style_block(assembled)
    change_summary = _latest_change_summary(db, source)
    adapter = get_adapter_for(Task.BLUEPRINT)

    updated: list[int] = []
    stale: list[int] = []
    warnings: list[str] = []

    for n in sorted(chapter_numbers):
        outline = _get_outline(db, project.id, n)
        if outline is None:
            warnings.append(f"第 {n} 章大纲不存在,跳过")
            continue
        if n <= source_chapter:
            warnings.append(f"第 {n} 章不在下游,跳过")
            continue

        # 相邻章节(重生成过的用新版)
        neighbors = []
        for m in (n - 1, n + 1):
            o = _get_outline(db, project.id, m)
            if o:
                neighbors.append(f"第{m}章《{o.title}》:{o.summary}")
        prompt = OUTLINE_REGENERATE_PROMPT.format(
            source_chapter=source_chapter,
            chapter_number=n,
            architecture_brief=_architecture_brief(project),
            change_summary=change_summary,
            new_source_outline=_fmt(outline_to_dict(source)),
            old_outline=_fmt(outline_to_dict(outline)),
            neighbor_outlines="\n".join(neighbors) or "(无)",
            reason=reasons.get(n, "上游剧情变更"),
            style_directives=style_block,
        )
        raw = await adapter.ask(prompt)
        parsed = parse_blueprint(raw)
        target = next((c for c in parsed if c.get("chapter_number") == n), None)
        if target is None:
            warnings.append(f"第 {n} 章重生成解析失败,保留旧大纲")
            continue

        for field in (
            "title", "chapter_role", "chapter_purpose", "suspense_level",
            "foreshadowing", "plot_twist_level", "summary",
            "characters_involved", "key_items", "scene_location",
        ):
            if field in target:
                setattr(outline, field, target[field])
        outline.content_hash = _outline_content_hash(outline_to_dict(outline))
        outline.current_version += 1
        db.add(
            OutlineVersion(
                outline_id=outline.id,
                version=outline.current_version,
                snapshot=outline_to_dict(outline),
                change_type="major",
                change_summary=f"级联重生成(源:第{source_chapter}章改动)",
            )
        )
        updated.append(n)

        ch = (
            db.query(Chapter)
            .filter(
                Chapter.project_id == project.id,
                Chapter.chapter_number == n,
                Chapter.final_content != "",
            )
            .first()
        )
        if ch:
            ch.is_stale = True
            ch.status = "stale"
            stale.append(n)

    db.flush()
    logger.info(
        "级联重生成完成: 源第%d章, 更新%s, 失配%s", source_chapter, updated, stale
    )
    return {"updated": updated, "stale_chapters": stale, "warnings": warnings}
