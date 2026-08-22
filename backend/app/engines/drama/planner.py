# app/engines/drama/planner.py
# -*- coding: utf-8 -*-
"""集数规划:把章节蓝图素材切成漫剧的「集」(钩子/梗概/卡点)。

改编输入用结构化事件(蓝图 summary + beats + 悬念),不喂全文——
Toonflow 的事件图谱思路,咱们的故事圣经/蓝图就是现成图谱,防长文本信息丢失。

重规划语义:只替换 source_chapter 落在本次范围内的旧集(其它集保留),
之后全表按源章号重排序号。想全部重来,选覆盖全部章节的范围即可。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaEpisode, DramaShot, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    MODE_DESC,
    coerce_int,
    clip,
    concept_block,
    episode_dict,
    outline_rows,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import EPISODE_PLAN_PROMPT

# 单次规划上限:防一次切出几百集
_MAX_EPISODES = 40


class DramaPlanError(ValueError):
    """规划的业务性错误(信息直接上屏)。"""


async def plan_episodes(
    db: Session,
    project: Project,
    from_ch: int,
    to_ch: int,
    mode: str,
    duration_s: int,
    progress=lambda s: None,
) -> list[dict]:
    outlines = outline_rows(db, project.id, from_ch, to_ch)
    if not outlines:
        raise DramaPlanError(f"第 {from_ch}-{to_ch} 章没有蓝图,无法规划。")

    lines = []
    for o in outlines:
        head = f"第{o.chapter_number}章《{o.title or ''}》:{(o.summary or '').strip()[:200]}"
        lines.append(head)
        if o.beats:
            beats = " / ".join(str(b) for b in o.beats if str(b or "").strip())
            if beats:
                lines.append(f"  节拍:{beats[:300]}")
        extra = []
        if o.suspense_level:
            extra.append(f"悬念:{o.suspense_level}")
        if o.foreshadowing:
            extra.append(f"伏笔:{clip(o.foreshadowing, 100)}")
        if extra:
            lines.append("  " + ";".join(extra))
    chapters_block = "【章节素材(按章号顺序)】\n" + "\n".join(lines)

    progress(f"AI 正在把 {len(outlines)} 章切成漫剧集(钩子/卡点)…")
    adapter = get_adapter_for(Task.DRAMA_PLAN, timeout=300)
    prompt = EPISODE_PLAN_PROMPT.format(
        duration_target_s=duration_s,
        mode_desc=MODE_DESC.get(mode, MODE_DESC["dialogue"]),
        title=project.title,
        genre=project.genre.strip() or "不限",
        concept_block=concept_block(project),
        chapters_block=chapters_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    # ---- normalize:clip + source_chapter 收敛回范围内 + 限条数 ----
    eps: list[dict] = []
    for item in (data.get("episodes") or []):
        if not isinstance(item, dict):
            continue
        src = coerce_int(item.get("source_chapter"), from_ch, lo=1)
        if not (from_ch <= src <= to_ch):
            src = from_ch
        title = clip(item.get("title"), 200)
        if not title:
            continue
        eps.append(
            {
                "title": title,
                "source_chapter": src,
                "hook": clip(item.get("hook"), 200),
                "recap": clip(item.get("recap"), 200),
                "cliffhanger": clip(item.get("cliffhanger"), 200),
            }
        )
        if len(eps) >= _MAX_EPISODES:
            break
    if not eps:
        raise DramaPlanError("规划结果为空,请重试或换一个章节范围。")

    # ---- 落库:删本次范围内的旧集(连分镜),保留范围外的,再全表重排序号 ----
    stale_ids = [
        e.id
        for e in db.query(DramaEpisode).filter(DramaEpisode.project_id == project.id).all()
        if from_ch <= e.source_chapter <= to_ch
    ]
    if stale_ids:
        db.query(DramaShot).filter(DramaShot.episode_id.in_(stale_ids)).delete(
            synchronize_session=False
        )
        db.query(DramaEpisode).filter(DramaEpisode.id.in_(stale_ids)).delete(
            synchronize_session=False
        )

    # 临时负数序号先满足 (project_id, ep_index) 唯一约束,flush 后统一重排
    for i, spec in enumerate(eps):
        db.add(
            DramaEpisode(
                project_id=project.id,
                ep_index=-(i + 1),
                title=spec["title"],
                source_chapter=spec["source_chapter"],
                hook=spec["hook"],
                recap=spec["recap"],
                cliffhanger=spec["cliffhanger"],
                mode=mode,
                duration_target_s=duration_s,
                script={},
                status="planned",
            )
        )
    db.flush()  # 拿到新行 id

    _reindex_episodes(db, project.id)
    db.commit()
    return [episode_dict(e) for e in _ordered_episodes(db, project.id)]


def _ordered_episodes(db: Session, project_id: int) -> list[DramaEpisode]:
    """按 (source_chapter, id) 稳定排序的全项目集列表。"""
    rows = (
        db.query(DramaEpisode)
        .filter(DramaEpisode.project_id == project_id)
        .order_by(DramaEpisode.source_chapter, DramaEpisode.id)
        .all()
    )
    return rows


def _reindex_episodes(db: Session, project_id: int) -> None:
    for i, row in enumerate(_ordered_episodes(db, project_id), start=1):
        row.ep_index = i
