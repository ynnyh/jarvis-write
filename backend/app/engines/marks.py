# app/engines/marks.py
# -*- coding: utf-8 -*-
"""跨章标记批修:作者边读边攒的「这里不行」标记,一句总描述驱动全书成批改。

与②档「按批注改」(api/chapters/revision.py)同一条改写链路——逐标记复用
polish_fragment(锁情节、只改文笔),区别只在作用域与驱动方式:
  - 按批注改:单章、每条批注各带各的意见,当场发;
  - 全书批修:跨章、一句总描述统一指挥(如「所有铁锈玫瑰的描写全换掉,
    别再用自残动作」),叠加每条标记自己的意见。

纪律:job 只产出待验收的替换对,**绝不落库**——与按批注改一致,应用走前端
逐条 diff 验收 → paraEdit 快照守卫写回(自动留版本快照),误改风险最低。
快照对不上(正文被改动过)的标记跳过并计入 stale,不拖垮整批。
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterMark, Outline, Project

# 单条意见/总描述的长度上限(与 AnnotationItem.note 同量级,防 prompt 膨胀)
_MAX_DIRECTIVE = 1000
_MAX_NOTE = 200


def split_paras(text: str) -> list[str]:
    """正文分段:与前端 splitParas 同口径(\n 分段、trim、去空行)。

    标记的 para_idx 与快照都基于这份口径,后端定位段落必须逐字一致,
    否则快照守卫会误判失效。
    """
    return [p.strip() for p in re.split(r"\n+", text) if p.strip()]


def merge_note(directive: str, note: str) -> str:
    """总描述 + 该处意见合成 polish_fragment 的 direction(总描述在前,权威更高)。"""
    parts = [p.strip() for p in (directive, note) if p.strip()]
    return "\n".join(parts)


async def revise_marks(
    db: Session, project_id: int, directive: str, progress=None
) -> dict:
    """全书批修核心:逐章逐标记锁情节改写,产出待验收替换对(不落库)。

    返回 {total, stale, chapters: [{chapter_number, pairs: [...]}]};pair 形如
    {mark_id, para_idx, old, new, notes, ok}。快照失配 → ok=False 计入 stale;
    单条 LLM 失败同样只标该条 ok=False,不拖垮整批(与按批注改同语义)。
    调用方(spawn_job 的 worker)自备 SessionLocal,别拿请求 session 跨 LLM 调用。
    """
    from app.engines.polish import polish_fragment
    from app.engines.tendency.assembler import voice_block_of

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响批修
                pass

    directive = (directive or "").strip()[:_MAX_DIRECTIVE]
    project = db.get(Project, project_id)
    voice_block = voice_block_of(project.global_tendency) if project else ""

    marks = (
        db.query(ChapterMark)
        .filter(ChapterMark.project_id == project_id, ChapterMark.status == "open")
        .order_by(ChapterMark.chapter_number, ChapterMark.para_idx, ChapterMark.id)
        .all()
    )
    if not marks:
        return {"total": 0, "stale": 0, "chapters": []}

    by_chapter: dict[int, list[ChapterMark]] = defaultdict(list)
    chapters = {
        c.chapter_number: c
        for c in db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number.in_({m.chapter_number for m in marks}),
            Chapter.final_content != "",
        )
        .all()
    }
    for m in marks:
        if m.chapter_number in chapters:
            by_chapter[m.chapter_number].append(m)

    stale = 0
    total = 0
    out: list[dict] = []
    for n in sorted(by_chapter):
        ch = chapters[n]
        paras = split_paras(ch.final_content)
        outline = (
            db.query(Outline)
            .filter(Outline.project_id == project_id, Outline.chapter_number == n)
            .first()
        )
        summary = outline.summary if outline else ""
        pairs: list[dict] = []
        ms = by_chapter[n]
        for i, m in enumerate(ms, 1):
            total += 1
            _report(f"第 {n} 章:正在改 {i}/{len(ms)} 处标记")
            para = paras[m.para_idx] if 0 <= m.para_idx < len(paras) else None
            if para is None or para != (m.snapshot or "").strip():
                stale += 1
                pairs.append({
                    "mark_id": m.id, "para_idx": m.para_idx, "old": m.snapshot,
                    "new": "", "notes": "原文已对不上(正文可能被改动过),这条已跳过",
                    "ok": False,
                })
                continue
            try:
                r = await polish_fragment(
                    para, merge_note(directive, m.note or ""), summary,
                    voice_block=voice_block,
                )
                pairs.append({
                    "mark_id": m.id, "para_idx": m.para_idx, "old": para,
                    "new": r["polished"], "notes": r.get("notes"), "ok": True,
                })
            except ValueError as exc:
                pairs.append({
                    "mark_id": m.id, "para_idx": m.para_idx, "old": para,
                    "new": "", "notes": str(exc), "ok": False,
                })
        out.append({"chapter_number": n, "pairs": pairs})

    return {"total": total, "stale": stale, "chapters": out}
