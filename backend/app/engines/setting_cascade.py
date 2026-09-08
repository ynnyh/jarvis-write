# app/engines/setting_cascade.py
# -*- coding: utf-8 -*-
"""设定级级联:设定(世界观硬规则等)变更 → 全书受影响扫描 → 段落级定点修提案。

与章级级联(cascade/,大纲改动波及下游)互补:设定全书有效,任何已写章节
都可能踩到;粒度从「章」细化到「段」——目标是定点修而不是整章重写。

三步流水线:
  1. diff_rules        确定性逐行 diff(不花 token);
  2. scan_setting_impact  章级粗筛(便宜,逐章判是否相关)→ 段级定位(只对
     命中章做,LLM 给逐字引文,**段号由后端在正文里反查**——不信任模型
     数数,防幻觉错位);
  3. patch_passages    对定位段产出改写提案,**不落库**——与全书批修同纪律,
     应用走前端逐条 diff 验收 → paraEdit 快照守卫写回 → 重抽圣经。

防幻觉纪律沿用一致性检查的先例:解析失败显式降级,绝不静默当"无影响"。
"""
from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models import Chapter, Outline
from app.engines.consistency.extractor import parse_llm_json_checked
from app.llm.router import Task, get_adapter_for

logger = logging.getLogger("jarvis-write.setting-cascade")

_MAX_CHANGES = 20          # 一次级联最多处理的规则变更条数(防 prompt 膨胀)
_MAX_PARAS = 160           # 段级定位单章最多喂的段数(超出截断留痕)
_MAX_TEXT = 14000          # 章级粗筛/段级定位单章正文上限(与规则扫描同量级)
_MIN_QUOTE = 6             # 引文反查的最短长度(再短极易撞上巧合)


def split_paras(text: str) -> list[str]:
    """正文分段:与 marks.split_paras / 前端 splitParas 同口径。

    para_idx 的唯一权威是这份口径,三处必须逐字一致。
    """
    return [p.strip() for p in re.split(r"\n+", text) if p.strip()]


def diff_rules(old_text: str, new_text: str) -> list[dict]:
    """规则钉板逐行 diff:返回 [{kind: changed|added|removed, old, new}]。

    确定性、零 token:行级 SequenceMatcher,块内替换按行配对,多出的
    行按纯增/纯删展开。空行与首尾空白不参与比较(编辑器常见噪音)。
    """
    old_lines = [l.strip() for l in (old_text or "").splitlines() if l.strip()]
    new_lines = [l.strip() for l in (new_text or "").splitlines() if l.strip()]
    changes: list[dict] = []
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        olds, news = old_lines[i1:i2], new_lines[j1:j2]
        if tag == "delete":
            changes += [{"kind": "removed", "old": o, "new": ""} for o in olds]
        elif tag == "insert":
            changes += [{"kind": "added", "old": "", "new": n} for n in news]
        else:  # replace:块内按行配对,长短不齐的尾部按纯增/纯删补齐
            for k in range(max(len(olds), len(news))):
                o = olds[k] if k < len(olds) else ""
                n = news[k] if k < len(news) else ""
                if not o:
                    changes.append({"kind": "added", "old": "", "new": n})
                elif not n:
                    changes.append({"kind": "removed", "old": o, "new": ""})
                else:
                    changes.append({"kind": "changed", "old": o, "new": n})
    return changes[:_MAX_CHANGES]


def _locate_para(paras: list[str], quote: str) -> int | None:
    """把 LLM 的逐字引文反查成段落号:精确包含优先,压缩空白后兜底。

    找不到返回 None(丢弃该条并计数)——宁可漏一条,不可错位改错段。
    """
    q = (quote or "").strip()
    if len(q) < _MIN_QUOTE:
        return None
    for i, p in enumerate(paras):
        if q in p:
            return i
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731 — 引文常被模型换行/加空格
    nq = norm(q)
    if len(nq) < _MIN_QUOTE:
        return None
    for i, p in enumerate(paras):
        if nq in norm(p):
            return i
    return None


def _changes_block(changes: list[dict]) -> str:
    from app.prompts.setting_cascade import _changes_block as _render
    return _render(changes)


def _written_chapters(db: Session, project_id: int) -> list[Chapter]:
    return (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.final_content != "",
            Chapter.status != "quarantined",  # 隔离章体检未过,不参与级联
        )
        .order_by(Chapter.chapter_number)
        .all()
    )


async def scan_setting_impact(
    db: Session, project_id: int, changes: list[dict], progress=None
) -> dict:
    """扫描设定变更对全书的影响:章级粗筛 + 段级定位。

    返回 {screened, affected_chapters: [{chapter_number, title, reason}],
    passages: [{chapter_number, para_idx, para_excerpt, quote, reason}],
    unlocated, failed}。
    单章失败显式计数不中断(与规则扫描同语义);粗筛解析失败按「未知」
    跳过并计入 failed——绝不静默当"不受影响"。
    """
    from app.prompts.setting_cascade import (
        SETTING_LOCATE_PROMPT,
        SETTING_SCREEN_PROMPT,
    )

    changes = changes[:_MAX_CHANGES]
    changes_block = _changes_block(changes)
    titles = {
        o.chapter_number: o.title
        for o in db.query(Outline).filter(Outline.project_id == project_id).all()
    }
    chapters = _written_chapters(db, project_id)
    total = len(chapters)
    adapter = get_adapter_for(Task.SETTING_IMPACT)

    # ---- 章级粗筛:概要级判断,便宜且覆盖全书 ----
    affected: list[dict] = []
    failed: list[int] = []
    for i, ch in enumerate(chapters, 1):
        if progress:
            progress(f"[{i}/{total}] 第 {ch.chapter_number} 章:影响粗筛")
        outline = (
            db.query(Outline)
            .filter(
                Outline.project_id == project_id,
                Outline.chapter_number == ch.chapter_number,
            )
            .first()
        )
        summary = (outline.summary if outline else "") or ch.final_content[:800]
        try:
            db.commit()  # 结束读事务,不拿快照跨 LLM 调用
            raw = await adapter.ask(
                SETTING_SCREEN_PROMPT.format(
                    changes_block=changes_block,
                    chapter_number=ch.chapter_number,
                    title=titles.get(ch.chapter_number) or f"第{ch.chapter_number}章",
                    summary=summary[:1200],
                )
            )
            data, err = parse_llm_json_checked(raw)
            if err:
                raise ValueError(err)
            if data.get("affected") is True:
                affected.append({
                    "chapter_number": ch.chapter_number,
                    "title": titles.get(ch.chapter_number) or "",
                    "reason": (data.get("reason") or "").strip()[:300],
                })
        except Exception as exc:  # noqa: BLE001 — 单章失败不中断整批
            db.rollback()
            failed.append(ch.chapter_number)
            logger.warning("设定粗筛第 %d 章失败: %s", ch.chapter_number, exc)

    # ---- 段级定位:只对命中章做,引文反查段号 ----
    passages: list[dict] = []
    unlocated = 0
    for a in affected:
        n = a["chapter_number"]
        ch = next(c for c in chapters if c.chapter_number == n)
        if progress:
            progress(f"第 {n} 章:定位冲突段落")
        paras = split_paras(ch.final_content)
        shown = paras[:_MAX_PARAS]
        numbered = "\n".join(f"[P{i}] {p}" for i, p in enumerate(shown))
        try:
            raw = await adapter.ask(
                SETTING_LOCATE_PROMPT.format(
                    changes_block=changes_block,
                    chapter_number=n,
                    title=a.get("title") or f"第{n}章",
                    numbered_text=numbered[:_MAX_TEXT],
                )
            )
            data, err = parse_llm_json_checked(raw)
            if err:
                raise ValueError(err)
        except Exception as exc:  # noqa: BLE001 — 定位失败不拖垮其他章
            logger.warning("设定定位第 %d 章失败: %s", n, exc)
            failed.append(n)
            continue
        seen: set[int] = set()
        for hit in data.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            idx = _locate_para(shown, hit.get("quote") or "")
            if idx is None or idx in seen:
                unlocated += 1  # 引文对不上正文:模型幻觉,丢弃
                continue
            seen.add(idx)
            passages.append({
                "chapter_number": n,
                "para_idx": idx,
                "para_excerpt": shown[idx][:80],
                "quote": (hit.get("quote") or "").strip()[:120],
                "reason": (hit.get("reason") or "").strip()[:300],
            })

    logger.info(
        "设定级联扫描:筛 %d 章,命中 %d 章 %d 段(引文落空 %d,失败 %d 章)",
        total, len(affected), len(passages), unlocated, len(set(failed)),
    )
    return {
        "screened": total,
        "affected_chapters": affected,
        "passages": passages,
        "unlocated": unlocated,
        "failed": sorted(set(failed)),
    }


async def patch_passages(
    db: Session, project_id: int, changes: list[dict],
    passages: list[dict], progress=None,
) -> dict:
    """对定位段产出改写提案(不落库):逐段对齐 revise_marks 的纪律与结构。

    入参 passages 来自 scan 结果(前端可删减),形如
    [{chapter_number, para_idx, ...}];返回
    {total, stale, chapters: [{chapter_number, pairs: [{para_idx, old, new, notes, ok}]}]}。
    段落与当前正文对不上 → ok=False 计入 stale(正文可能已被改动)。
    调用方(spawn_job 的 worker)自备 SessionLocal,别拿请求 session 跨 LLM。
    """
    from app.prompts.setting_cascade import SETTING_PATCH_PROMPT

    changes_block = _changes_block(changes[:_MAX_CHANGES])
    wanted: dict[int, list[dict]] = defaultdict(list)
    for p in passages or []:
        if isinstance(p, dict) and isinstance(p.get("chapter_number"), int) \
                and isinstance(p.get("para_idx"), int):
            wanted[p["chapter_number"]].append(p)

    chapters = {
        c.chapter_number: c
        for c in db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number.in_(set(wanted)),
            Chapter.final_content != "",
        )
        .all()
    }
    outline_summaries = {
        o.chapter_number: (o.summary or "")
        for o in db.query(Outline).filter(Outline.project_id == project_id).all()
    }
    adapter = get_adapter_for(Task.SETTING_PATCH)
    total = 0
    stale = 0
    out: list[dict] = []
    for n in sorted(wanted):
        ch = chapters.get(n)
        items = wanted[n]
        if ch is None:
            total += len(items)
            stale += len(items)
            out.append({"chapter_number": n, "pairs": [
                {"para_idx": p["para_idx"], "old": "", "new": "",
                 "notes": "该章没有可用正文", "ok": False} for p in items
            ]})
            continue
        paras = split_paras(ch.final_content)
        summary = outline_summaries.get(n, "")
        pairs: list[dict] = []
        for i, p in enumerate(items, 1):
            total += 1
            idx = p["para_idx"]
            if progress:
                progress(f"第 {n} 章:修订提案 {i}/{len(items)}")
            para = paras[idx] if 0 <= idx < len(paras) else None
            if para is None:
                stale += 1
                pairs.append({"para_idx": idx, "old": "", "new": "",
                              "notes": "段号超出正文范围", "ok": False})
                continue
            try:
                raw = await adapter.ask(
                    SETTING_PATCH_PROMPT.format(
                        changes_block=changes_block,
                        summary=summary[:600],
                        paragraph=para[:4000],
                    )
                )
                data, err = parse_llm_json_checked(raw)
                if err:
                    raise ValueError(err)
                new_para = (data.get("new_paragraph") or "").strip()
                if not new_para:
                    raise ValueError("模型未返回改写段落")
                pairs.append({
                    "para_idx": idx, "old": para, "new": new_para,
                    "notes": (data.get("notes") or "").strip()[:300], "ok": True,
                })
            except Exception as exc:  # noqa: BLE001 — 单条失败不拖垮整批
                logger.warning("设定修订第 %d 章第 %d 段失败: %s", n, idx, exc)
                pairs.append({"para_idx": idx, "old": para, "new": "",
                              "notes": f"改写失败:{exc}", "ok": False})
        out.append({"chapter_number": n, "pairs": pairs})

    logger.info("设定定点修提案: %d 段(stale %d)", total, stale)
    return {"total": total, "stale": stale, "chapters": out}
