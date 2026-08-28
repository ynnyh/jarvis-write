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
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chapter, Outline, OutlineVersion, Project
from app.engines.pipeline.blueprint_parser import parse_blueprint, validate_blueprint
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.engines.title_style import DEFAULT_TITLE_DIRECTIVE
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

# 流式进度用:统计已成形的「第N章」章节头个数。与解析器同源的宽松匹配——
# 允许行首 markdown 星号/空白 + 全角/半角数字,不要求后面的分隔符已吐出来
# (章节头一冒出来就算这章"开始生成了")。
_HEAD_RE = re.compile(r"^\s*\**\s*第\s*\d+\s*章", re.MULTILINE)


def _count_heads(text: str) -> int:
    return len(_HEAD_RE.findall(text))


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
            "beats",
            "characters_involved",
            "key_items",
            "scene_location",
        )
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _generate_chunk(
    adapter,
    prompt: str,
    *,
    expected: int,
    base_done: int,
    total: int,
    report,
) -> str:
    """生成一块蓝图,流式增量上报「已生成 N/总 章」。返回原始文本(未解析)。

    优先走流式:边收边数章节头,每多出一章就上报一次进度,让用户看到逐章生长,
    而不是干等一个不透明的批量调用。流式不可用(适配器无 stream / 出错 / 返回空)时,
    回落到一次性 ask()(带瞬时错误重试 + 空正文翻倍重试 + token 记账)。

    注:流式主路径不记 token 用量(与 _complete_via_stream 一致的取舍——流式拿不到
    准确 usage,记账允许为 0);回落的 ask() 分支照常记账。
    base_done=本次规划中之前已完成的章数;total=本次规划总章数(进度分母)。
    """
    try:
        parts: list[str] = []
        last_reported = -1
        async for delta in adapter.stream(adapter.to_messages(prompt)):
            if not delta:
                continue
            parts.append(delta)
            # 节流:章节头必然在行首,只在增量跨行时才扫描,避免每个 token 都全文扫
            if "\n" not in delta:
                continue
            n = _count_heads("".join(parts))
            done = min(base_done + min(n, expected), total)
            if done > last_reported:
                last_reported = done
                report(f"已生成 {done}/{total} 章")
        raw = "".join(parts).strip()
        if raw:
            return raw
        logger.warning("蓝图流式返回空,回落一次性调用")
    except Exception as exc:  # noqa: BLE001 — 流式任何异常都回落,不影响生成
        logger.warning("蓝图流式失败,回落一次性调用: %s", exc)
    return await adapter.ask(prompt)


async def generate_blueprint(
    *,
    novel_architecture: str,
    number_of_chapters: int,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    progress=None,
    start_chapter: int = 1,
    end_chapter: int | None = None,
    previous_tail: str = "",
    title_directive: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """分块生成章节蓝图。返回 (章节 dict 列表, 警告列表)。纯生成,不落库。

    滚动规划:传 start_chapter/end_chapter 只生成该区间(展开下一卷);
    previous_tail 为上一卷蓝图尾部文本,保证跨卷衔接。
    number_of_chapters 始终是全书总章数(prompt 里的全局语境)。
    progress: 可选回调 fn(stage_text),每块生成前/流式逐章/解析后上报(异步任务进度用)。
    title_directive: 章节标题风格导向(预设+自由文本已在上游解析);空则回落默认(plain)。
    """
    title_directive = (title_directive or "").strip() or DEFAULT_TITLE_DIRECTIVE

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    adapter = get_adapter_for(Task.BLUEPRINT)

    last = end_chapter or number_of_chapters
    run_total = last - start_chapter + 1  # 本次规划的章数,流式进度分母
    all_chapters: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    raw_accumulated = previous_tail

    start = start_chapter
    while start <= last:
        end = min(start + CHUNK_SIZE - 1, last)
        logger.info("蓝图生成:第 %d-%d 章...", start, end)
        _report(f"生成中:第 {start}-{end} 章(本次规划到第 {last} 章 / 全书 {number_of_chapters} 章)")

        expected = end - start + 1
        min_ok = max(1, int(expected * _CHUNK_MIN_RATIO))

        valid: list[dict[str, Any]] = []
        raw_warnings: list[str] = []
        raw = ""
        # 块内续写游标:欠章(输出超长被截断是主因——设定越肥,每章简述写得越长,
        # 20 章一块越容易顶到模型单次输出上限)时只补「最后一章之后」的尾部区间,
        # 而不是原样重摇整块:同样的输入会得到同样的截断,重摇是烧时间。
        seg_start = start
        attempts = 0
        while seg_start <= end and attempts < _CHUNK_MAX_ATTEMPTS:
            if start == 1 and seg_start == start and end == number_of_chapters:
                # 一块装得下,用整书模板
                prompt = CHAPTER_BLUEPRINT_PROMPT.format(
                    novel_architecture=novel_architecture,
                    number_of_chapters=number_of_chapters,
                    style_directives=style_block,
                    title_directive=title_directive,
                )
            else:
                prompt = CHUNKED_BLUEPRINT_PROMPT.format(
                    novel_architecture=novel_architecture,
                    start_chapter=seg_start,
                    end_chapter=end,
                    previous_blueprint_tail=raw_accumulated[-_TAIL_CHARS:] or "(首块,无)",
                    style_directives=style_block,
                    title_directive=title_directive,
                )

            expected_seg = end - seg_start + 1
            raw = await _generate_chunk(
                adapter,
                prompt,
                expected=expected_seg,
                base_done=len(all_chapters) + len(valid),
                total=run_total,
                report=_report,
            )
            raw_accumulated += "\n" + raw
            seg_valid, seg_warn = validate_blueprint(parse_blueprint(raw), seg_start, end)
            raw_warnings.extend(seg_warn)
            valid.extend(seg_valid)

            if len(seg_valid) >= expected_seg:
                break
            attempts += 1
            last_num = max(
                (
                    int(c["chapter_number"])
                    for c in seg_valid
                    if c.get("chapter_number")
                ),
                default=seg_start - 1,
            )
            seg_start = max(seg_start, last_num + 1)
            if seg_start <= end:
                logger.warning(
                    "蓝图块 %d-%d 欠章(已解析至第 %d 章),第 %d/%d 次续补 %d-%d...",
                    start, end, last_num, attempts, _CHUNK_MAX_ATTEMPTS, seg_start, end,
                )
                _report(
                    f"第 {start}-{end} 章输出不完整(解析到第 {last_num} 章),"
                    f"自动续补第 {seg_start}-{end} 章…"
                )

        # 段间续补可能重叠:同章以后者为准(与 validate_blueprint 同口径);
        # 缺章只在块级算一次——续补过程中间态的缺章不该刷成警告
        merged: dict[int, dict[str, Any]] = {}
        for c in valid:
            num = int(c.get("chapter_number") or 0)
            if num in merged:
                raw_warnings.append(f"第 {num} 章重复出现,以后者为准")
            merged[num] = c
        valid = [merged[n] for n in sorted(merged)]
        warnings = [w for w in raw_warnings if not w.startswith("缺少章节")]
        missing = [n for n in range(start, end + 1) if n not in merged]
        if missing:
            warnings.append(f"缺少章节: {missing}")

        if len(valid) < min_ok:
            raise RuntimeError(
                f"蓝图块 {start}-{end} 生成失败:含自动续补共 {attempts} 次调用后仍只有 "
                f"{len(valid)}/{expected} 章。多半是模型单次输出上限装不下这块蓝图"
                "(设定越肥每章写得越长),或返回格式崩坏;请重试一次,或在「设置」里"
                "换上下文/输出上限更大的模型。"
            )

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
            # 蓝图(按新架构)重铺且本章内容变了 → 旧正文本体对不上,标失配,
            # 与 per-chapter 级联引擎一致(differ 里也是这么标记的)。
            _mark_chapter_stale(db, project.id, num)

        outline.title = ch.get("title", "")
        outline.chapter_role = ch.get("chapter_role", "")
        outline.chapter_purpose = ch.get("chapter_purpose", "")
        outline.suspense_level = ch.get("suspense_level", "")
        outline.foreshadowing = ch.get("foreshadowing", "")
        outline.plot_twist_level = ch.get("plot_twist_level", "")
        outline.summary = ch.get("summary", "")
        outline.beats = ch.get("beats", [])
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
    # 蓝图已按最新架构重铺 → 消退「架构换了、大纲还挂在旧架构上」的标记
    project.outline_stale = False
    db.flush()
    return saved


def _mark_chapter_stale(db: Session, project_id: int, chapter_number: int) -> None:
    """该章原正文本体对不上了:标失配,写作页据此提示「是否重写」。"""
    db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.chapter_number == chapter_number,
        Chapter.final_content != "",
    ).update(
        {Chapter.is_stale: True, Chapter.status: "stale"},
        synchronize_session=False,
    )
