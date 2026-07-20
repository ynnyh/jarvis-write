# app/engines/pipeline/blueprint.py
# -*- coding: utf-8 -*-
"""章节蓝图分块生成与落库。

借鉴 AI_NovelGenerator 的 chunked blueprint 思路:章节多时分块生成,
每块携带前一块尾部作为衔接上下文,避免超长与断裂。

落库时:
- 每章一行 outlines,计算 content_hash(级联引擎判变更用)
- 同时写入 OutlineVersion v1 快照(级联引擎 diff 的基线)
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Outline, OutlineVersion, Project
from app.engines.pipeline.blueprint_parser import parse_blueprint, validate_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts import CHAPTER_BLUEPRINT_PROMPT, CHUNKED_BLUEPRINT_PROMPT
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.pipeline")

# 单块最多生成多少章。经验值:每章蓝图约 150-250 token,
# 20 章一块在多数模型的输出上限内且质量稳定。
CHUNK_SIZE = 20

# 衔接上下文取前一块尾部多少字符
_TAIL_CHARS = 1200

# 单块解析失败(空/大幅欠章)时的重试次数。LLM 偶发返回格式崩坏/截断,
# 重试通常能恢复;仍失败则明确报错,不让空蓝图流入逐章生成。
_CHUNK_MAX_ATTEMPTS = 3

# 一块解析出的章数低于应有章数的这个比例,视为本块失败需重试。
_CHUNK_MIN_RATIO = 0.6


def _outline_content_hash(data: dict[str, Any]) -> str:
    """对大纲的语义字段计算指纹,供级联引擎判断"是否真的变了"。"""
    material = {
        k: data.get(k, "")
        for k in (
            "title",
            "chapter_role",
            "chapter_purpose",
            "suspense_level",
            "foreshadowing",
            "plot_twist_level",
            "summary",
            "characters_involved",
            "key_items",
            "scene_location",
        )
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def generate_blueprint(
    *,
    novel_architecture: str,
    number_of_chapters: int,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    progress=None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """分块生成全书章节蓝图。返回 (章节 dict 列表, 警告列表)。纯生成,不落库。

    progress: 可选回调 fn(stage_text),每块生成前/解析后各报一次(异步任务进度用)。
    """

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    adapter = get_adapter_for(Task.BLUEPRINT)

    all_chapters: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    raw_accumulated = ""

    start = 1
    while start <= number_of_chapters:
        end = min(start + CHUNK_SIZE - 1, number_of_chapters)
        logger.info("蓝图生成:第 %d-%d 章...", start, end)
        _report(f"生成中:第 {start}-{end} 章 / 共 {number_of_chapters} 章")

        if start == 1 and end == number_of_chapters:
            # 一块装得下,用整书模板
            prompt = CHAPTER_BLUEPRINT_PROMPT.format(
                novel_architecture=novel_architecture,
                number_of_chapters=number_of_chapters,
                style_directives=style_block,
            )
        else:
            prompt = CHUNKED_BLUEPRINT_PROMPT.format(
                novel_architecture=novel_architecture,
                start_chapter=start,
                end_chapter=end,
                previous_blueprint_tail=raw_accumulated[-_TAIL_CHARS:] or "(首块,无)",
                style_directives=style_block,
            )

        expected = end - start + 1
        min_ok = max(1, int(expected * _CHUNK_MIN_RATIO))

        valid: list[dict[str, Any]] = []
        warnings: list[str] = []
        raw = ""
        for attempt in range(1, _CHUNK_MAX_ATTEMPTS + 1):
            raw = await adapter.ask(prompt)
            parsed = parse_blueprint(raw)
            valid, warnings = validate_blueprint(parsed, start, end)
            if len(valid) >= min_ok:
                break
            logger.warning(
                "蓝图块 %d-%d 第 %d/%d 次仅解析出 %d/%d 章(raw %d 字),重试...",
                start, end, attempt, _CHUNK_MAX_ATTEMPTS, len(valid), expected, len(raw),
            )
        else:
            # 重试用尽仍不达标:明确报错,不让空/残缺蓝图静默流入逐章生成
            raise RuntimeError(
                f"蓝图块 {start}-{end} 生成失败:{_CHUNK_MAX_ATTEMPTS} 次尝试仅解析出 "
                f"{len(valid)}/{expected} 章。最后一次返回长度 {len(raw)} 字。"
                "可能是模型返回格式崩坏或被截断,请重试或换模型。"
            )

        raw_accumulated += "\n" + raw
        all_chapters.extend(valid)
        all_warnings.extend(warnings)
        _report(f"第 {start}-{end} 章解析完成(累计 {len(all_chapters)} 章)")
        if warnings:
            logger.warning("蓝图块 %d-%d 警告: %s", start, end, warnings)

        start = end + 1

    logger.info("蓝图生成完成:共 %d 章,%d 条警告。", len(all_chapters), len(all_warnings))
    return all_chapters, all_warnings


def save_blueprint(
    db: Session, project: Project, chapters: list[dict[str, Any]]
) -> list[Outline]:
    """蓝图落库。已存在的章节大纲(同 project 同章号)会被覆盖并升版本。"""
    existing = {
        o.chapter_number: o
        for o in db.query(Outline).filter(Outline.project_id == project.id)
    }

    saved: list[Outline] = []
    for ch in chapters:
        num = ch["chapter_number"]
        content_hash = _outline_content_hash(ch)

        outline = existing.get(num)
        if outline is None:
            outline = Outline(project_id=project.id, chapter_number=num)
            db.add(outline)
            version = 1
        else:
            if outline.content_hash == content_hash:
                saved.append(outline)  # 内容没变,不升版本
                continue
            version = outline.current_version + 1

        outline.title = ch.get("title", "")
        outline.chapter_role = ch.get("chapter_role", "")
        outline.chapter_purpose = ch.get("chapter_purpose", "")
        outline.suspense_level = ch.get("suspense_level", "")
        outline.foreshadowing = ch.get("foreshadowing", "")
        outline.plot_twist_level = ch.get("plot_twist_level", "")
        outline.summary = ch.get("summary", "")
        outline.characters_involved = ch.get("characters_involved", [])
        outline.key_items = ch.get("key_items", [])
        outline.scene_location = ch.get("scene_location", "")
        outline.content_hash = content_hash
        outline.current_version = version
        db.flush()  # 拿到 outline.id

        # 版本快照:级联引擎 diff 的基线
        db.add(
            OutlineVersion(
                outline_id=outline.id,
                version=version,
                snapshot=ch,
                change_type="minor",
                change_summary="蓝图生成" if version == 1 else "蓝图重新生成",
            )
        )
        saved.append(outline)

    project.status = "writing"
    db.flush()
    return saved
