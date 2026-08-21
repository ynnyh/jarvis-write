# app/engines/outline_retitle.py
# -*- coding: utf-8 -*-
"""章节标题润色:基于本章大纲生成若干候选标题,供作者挑选。

作者觉得生成的章节标题不合适(常见:太夸张 / 标题党)时用。
只产候选、不落库;前端选定后走 editOutline 只改 title —— 纯展示性改动,
不标正文失配、不触发级联(见 cascade.differ._COSMETIC_FIELDS)。
"""
from __future__ import annotations

import logging
import re

from app.engines.consistency.extractor import parse_llm_json
from app.engines.title_style import DEFAULT_TITLE_DIRECTIVE
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import BATCH_RETITLE_PROMPT, CHAPTER_RETITLE_PROMPT

logger = logging.getLogger("jarvis-write.outline-retitle")

# 作者没写具体要求时的默认导向:直接对应「让 AI 换个不夸张的」按钮。
# 收敛到 title_style 单一来源,与蓝图批量生成/批量重出标题共用同一句默认导向。
_DEFAULT_DIRECTIVE = DEFAULT_TITLE_DIRECTIVE

# 批量重拟标题时,每次 LLM 调用最多带多少章(避免长书一次调用输出被截断)。
# 标题只有短串、输出很轻,25 章一批在多数模型输出上限内且稳定。
_RETITLE_BATCH = 25

# 候选标题可能出现的键名(模型常不照约定的 "titles" 输出)
_TITLE_KEYS = ("titles", "候选标题", "候选", "titles_list", "suggestions", "options", "list", "标题")
# 标题两端要剥掉的包裹符(书名号/引号/中文括号/空白,含全角)
_STRIP_CHARS = "《》「」『』\"'“”‘’ 　"


def _coerce_title_items(parsed) -> list:
    """从 parse_llm_json 结果里取候选列表,兼容常见跑偏形态,取不到返回 []:
    - 约定格式 {"titles": [...]}
    - 裸数组 [...](模型忽略外层键直接给数组 —— 最常见)
    - 别名键 {"候选标题": [...]} / {"suggestions": [...]} 等
    - 键名完全没照约定时,退而取字典里第一个非空列表值
    """
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in _TITLE_KEYS:
            v = parsed.get(k)
            if isinstance(v, list) and v:
                return v
        for v in parsed.values():  # 键名没照约定:第一个非空列表值兜底
            if isinstance(v, list) and v:
                return v
    return []


def _regex_fallback_titles(raw: str) -> list[str]:
    """结构化解析彻底失败时的兜底:从最后一个 [...] 数组里抓带引号的字符串。
    专治推理模型把花括号混进思考、导致 parse_llm_json 的外层大括号截取失效的情况。"""
    arrays = re.findall(r"\[[^\[\]]*\]", raw or "", re.DOTALL)
    for arr in reversed(arrays):  # 结论通常在最后
        vals = [a or b for a, b in re.findall(r'"([^"]{1,40})"|“([^”]{1,40})”', arr)]
        vals = [v.strip() for v in vals if v.strip()]
        if vals:
            return vals
    return []


def _item_to_title(item) -> str:
    """单个候选项归一成标题串:兼容列表项是 {"title": ...} 之类对象的情况;
    剥包裹符、截长度。取不到返回空串(由调用方过滤)。"""
    if isinstance(item, dict):
        item = item.get("title") or item.get("标题") or next(
            (v for v in item.values() if isinstance(v, str)), "")
    return str(item).strip().strip(_STRIP_CHARS).strip()[:30]


async def suggest_chapter_titles(
    *,
    chapter_number: int,
    architecture_brief: str,
    outline_block: str,
    current_title: str,
    directive: str = "",
    count: int = 5,
) -> list[str]:
    """产出 count 个候选章节标题(已去重 / 去空 / 去掉与当前完全相同的 / 截断长度)。

    解析对模型输出高度容错:不止认约定的 {"titles": [...]},还认裸数组、别名键、
    对象列表项,并在结构化失败时用正则从 [...] 兜底 —— 只要模型给了一串标题,
    就尽力捞出来,不轻易 400。低温调用进一步提高结构化输出的稳定性。
    """
    directive = (directive or "").strip() or _DEFAULT_DIRECTIVE
    raw = await get_adapter_for(Task.BLUEPRINT, temperature=0.5).ask(
        CHAPTER_RETITLE_PROMPT.format(
            chapter_number=chapter_number,
            architecture_brief=architecture_brief,
            outline_block=outline_block,
            current_title=(current_title or "").strip() or "(无)",
            directive=directive,
            count=count,
        )
    )
    items = _coerce_title_items(parse_llm_json(raw)) or _regex_fallback_titles(raw)
    if not items:
        logger.warning("章节标题候选解析失败,原文前200字: %s", (raw or "")[:200])
        raise ValueError("AI 返回的标题候选无法解析,请重试")

    cur = (current_title or "").strip()
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        t = _item_to_title(it)
        if not t or t == cur or t in seen:
            continue
        seen.add(t)
        out.append(t)
    if not out:
        # 全被过滤(如模型只回了与当前雷同的标题):别静默返回空,抛错让路由转 400。
        raise ValueError("AI 没给出可用的候选标题,请重试")
    return out[:count]


# ---------- 批量重拟标题(一键换一批,不动剧情)----------


def _parse_chapter_num(v) -> int | None:
    """从任意值里抠出章号数字(容忍 "第3章"/"3"/3/"ch3" 等),取不到返回 None。"""
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def _batch_digest(chapters: list[dict]) -> str:
    """把多章渲染成「章号 + 现标题 + 简述」的清单,喂给批量重拟 prompt。"""
    lines = []
    for c in chapters:
        summary = (c.get("summary") or "").strip().replace("\n", " ")
        title = (c.get("title") or "").strip() or "(无)"
        lines.append(
            f"第{c['chapter_number']}章 现标题《{title}》 内容:{summary[:70] or '(无简述)'}"
        )
    return "\n".join(lines)


def _coerce_batch(parsed, ordered_numbers: list[int]) -> dict[int, str]:
    """把模型输出归一成 {章号: 新标题}。对跑偏形态高度容错:
    - {"titles": [{"chapter": N, "title": "..."}]} / 裸对象数组(约定格式)
    - {"1": "t1", "2": "t2"}(直接拿章号做键)
    - ["t1", "t2", ...](纯标题数组,按给定顺序位置对应章号)
    """
    # 形态 A:dict 且键本身像章号(不含约定的 titles 等外层键)→ 直接映射
    if isinstance(parsed, dict) and not any(k in parsed for k in _TITLE_KEYS):
        num_keyed: dict[int, str] = {}
        for k, v in parsed.items():
            num = _parse_chapter_num(k)
            t = _item_to_title(v)
            if num is not None and t:
                num_keyed[num] = t
        if num_keyed:
            return num_keyed

    items = _coerce_title_items(parsed)
    obj_mapped: dict[int, str] = {}
    positional: list[str] = []
    for it in items:
        if isinstance(it, dict):
            num = None
            for nk in ("chapter", "chapter_number", "章", "章号", "n", "number", "序号", "index"):
                if nk in it:
                    num = _parse_chapter_num(it.get(nk))
                    break
            t = _item_to_title(it)
            if not t:
                continue
            if num is not None:
                obj_mapped[num] = t
            else:
                positional.append(t)
        else:
            t = _item_to_title(it)
            if t:
                positional.append(t)
    if obj_mapped:
        return obj_mapped
    # 形态 C:纯位置数组 → 按传入顺序贴章号(仅当拿不到对象映射时)
    return {num: t for num, t in zip(ordered_numbers, positional)}


async def suggest_all_chapter_titles(
    *,
    architecture_brief: str,
    chapters: list[dict],
    directive: str = "",
    progress=None,
) -> list[dict]:
    """为多章批量重拟标题。返回有变化的项 [{chapter_number, old_title, new_title}]。

    chapters: [{chapter_number, title, summary}](按章号顺序)。
    长书按 _RETITLE_BATCH 分批调用,progress(stage) 上报「重拟 N/M 章标题」。
    只产建议、不落库;调用方逐章走 apply_outline_edit 只改 title(cosmetic,不动正文)。
    解析对模型输出高度容错;整体一条都没解析出来才抛错(交路由转 400)。
    """
    directive = (directive or "").strip() or _DEFAULT_DIRECTIVE
    total = len(chapters)
    results: list[dict] = []
    total_mapped = 0

    def _report(done: int) -> None:
        if progress:
            try:
                progress(f"重拟 {min(done, total)}/{total} 章标题")
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    for i in range(0, total, _RETITLE_BATCH):
        batch = chapters[i : i + _RETITLE_BATCH]
        _report(i)
        raw = await get_adapter_for(Task.BLUEPRINT, temperature=0.6).ask(
            BATCH_RETITLE_PROMPT.format(
                architecture_brief=architecture_brief,
                directive=directive,
                chapters_block=_batch_digest(batch),
            )
        )
        ordered = [c["chapter_number"] for c in batch]
        mapping = _coerce_batch(parse_llm_json(raw), ordered)
        if not mapping:  # 本批彻底没解析出来:兜底从 [...] 里按位置捞
            mapping = {
                num: t for num, t in zip(ordered, _regex_fallback_titles(raw))
            }
        total_mapped += len(mapping)
        for c in batch:
            new_title = (mapping.get(c["chapter_number"]) or "").strip()
            old_title = (c.get("title") or "").strip()
            if new_title and new_title != old_title:
                results.append({
                    "chapter_number": c["chapter_number"],
                    "old_title": old_title,
                    "new_title": new_title,
                })
        _report(i + len(batch))

    if total_mapped == 0:
        logger.warning("批量标题解析失败,共 %d 章无一解析成功", total)
        raise ValueError("AI 返回的标题无法解析,请重试")
    return results
