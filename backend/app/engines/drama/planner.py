# app/engines/drama/planner.py
# -*- coding: utf-8 -*-
"""集数规划:把章节素材切成漫剧的「集」(钩子/梗概/卡点)。

改编输入优先用结构化事件(蓝图 summary + beats + 悬念),不喂全文——
Toonflow 的事件图谱思路,咱们的故事圣经/蓝图就是现成图谱,防长文本信息丢失。
**无蓝图兜底**:approved 章节但没铺蓝图时,用每章正文开头拼素材——
外导入书、懒得铺蓝图的书也能走漫剧线。
两种来源都会追加章末契约的未决线索(open_threads):它是「下一章该接什么」
的权威记录,是钩子/卡点最准的原料。

重规划语义:只替换任一源章号落在本次范围内的旧集(其它集保留),
之后全表按源章号重排序号。想全部重来,选覆盖全部章节的范围即可。

一集可以由数章合并而来(过渡章),源章号存 source_chapters 列表,
source_chapter 存其最小值(排序与范围替换的锚)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState, DramaEpisode, DramaShot, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    MODE_DESC,
    approved_chapter_numbers,
    book_block,
    chapter_final_text,
    coerce_int,
    clip,
    concept_block,
    episode_dict,
    episode_source_chapters,
    outline_rows,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import EPISODE_PLAN_PROMPT

# 单次规划上限:防一次切出几百集
_MAX_EPISODES = 40
# 无蓝图兜底时,每章正文喂开头多少字(情节开局足够,控窗)
_FALLBACK_HEAD_CHARS = 300
# 每章最多带几条章末未决线索(钩子/卡点原料,多了变流水账)
_THREADS_PER_CHAPTER = 2


class DramaPlanError(ValueError):
    """规划的业务性错误(信息直接上屏)。"""


def _banned_block(db: Session, project_id: int) -> str:
    """作者雷区块(桥段台账的 banned 行)——切集的钩子/卡点是再创作自由度最大的
    环节,最容易把作者写烦的桥段换个说法又写回来。只约束**新设计**的部分:
    源正文里已有的内容不在此列(那是剧本忠实改编的对象,正文修完自然干净)。"""
    from app.engines.consistency.motifs import banned_rows

    rows = banned_rows(db, project_id)
    if not rows:
        return ""
    lines = [f"  - {r.label}" + (f":{r.detail}" if r.detail else "") for r in rows]
    return (
        "【作者雷区(再创作硬约束:设计钩子/卡点/集标题时不得使用以下桥段或意象,"
        "换措辞也算;源正文里已有的内容不在此列,按正文忠实改编)】\n"
        + "\n".join(lines) + "\n"
    )


def _chapter_threads(db: Session, project_id: int, n: int) -> list[str]:
    """第 n 章章末契约的未决线索(open_threads,最新优先取前 N);无契约 → 空表。"""
    from app.engines.pipeline.handoff import _fresh_contract

    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        return []
    state = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
    contract = _fresh_contract(state, ch) if state is not None else None
    if not contract:
        return []
    return [
        str(t).strip()
        for t in (contract.get("open_threads") or [])
        if str(t or "").strip()
    ][:_THREADS_PER_CHAPTER]


def _chapter_material(
    db: Session, project_id: int, from_ch: int, to_ch: int
) -> tuple[str, int]:
    """章节素材块:有蓝图用蓝图,没蓝图用正文开头兜底;都追加未决线索。

    两类素材可以混存(铺了前几卷蓝图的书,后面未铺的章走正文兜底)。
    返回 (素材块, 有素材的章数)。
    """
    outlined = {o.chapter_number: o for o in outline_rows(db, project_id, from_ch, to_ch)}
    approved = [
        n for n in approved_chapter_numbers(db, project_id) if from_ch <= n <= to_ch
    ]
    nums = sorted(set(outlined) | set(approved))

    lines: list[str] = []
    for n in nums:
        o = outlined.get(n)
        parts: list[str] = []
        if o is not None:
            parts.append(f"第{n}章《{o.title or ''}》:{(o.summary or '').strip()[:200]}")
            if o.beats:
                beats = " / ".join(str(b) for b in o.beats if str(b or "").strip())
                if beats:
                    parts.append(f"  节拍:{beats[:300]}")
            extra = []
            if o.suspense_level:
                extra.append(f"悬念:{o.suspense_level}")
            if o.foreshadowing:
                extra.append(f"伏笔:{clip(o.foreshadowing, 100)}")
            if extra:
                parts.append("  " + ";".join(extra))
        else:
            body = chapter_final_text(db, project_id, n)
            if not body:
                continue  # 无蓝图也无正文:跳过
            parts.append(f"第{n}章(无蓝图,取正文开头):{body[:_FALLBACK_HEAD_CHARS]}")
        threads = _chapter_threads(db, project_id, n)
        if threads:
            parts.append("  未决线索:" + " / ".join(threads))
        lines.append("\n".join(parts))
    if not lines:
        return "", 0
    return "【章节素材(按章号顺序)】\n" + "\n".join(lines), len(lines)


async def plan_episodes(
    db: Session,
    project: Project,
    from_ch: int,
    to_ch: int,
    mode: str,
    duration_s: int,
    progress=lambda s: None,
) -> list[dict]:
    chapters_block, material_count = _chapter_material(db, project.id, from_ch, to_ch)
    if not material_count:
        raise DramaPlanError(
            f"第 {from_ch}-{to_ch} 章既没有蓝图也没有定稿正文,无法规划。"
            "先在写作区铺蓝图/生成正文,或改选其它章节范围。"
        )

    progress(f"AI 正在把 {material_count} 章切成漫剧集(钩子/卡点)…")
    adapter = get_adapter_for(Task.DRAMA_PLAN, timeout=300)
    prompt = EPISODE_PLAN_PROMPT.format(
        duration_target_s=duration_s,
        mode_desc=MODE_DESC.get(mode, MODE_DESC["dialogue"]),
        title=project.title,
        genre=project.genre.strip() or "不限",
        # 书级资产(本书基因/创作偏好)与作者雷区并入 concept_block 收口:零模板改动
        concept_block=(
            concept_block(project) + book_block(project) + _banned_block(db, project.id)
        ),
        chapters_block=chapters_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    # ---- normalize:clip + source_chapters 收敛回范围内 + 限条数 ----
    eps: list[dict] = []
    for item in (data.get("episodes") or []):
        if not isinstance(item, dict):
            continue
        srcs = _parse_source_chapters(item, from_ch, to_ch)
        title = clip(item.get("title"), 200)
        if not title:
            continue
        eps.append(
            {
                "title": title,
                "source_chapter": srcs[0],
                "source_chapters": srcs,
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
    # 「范围内」= 任一源章号落在本次范围(并集的集只要沾到就重切,免得同一章被两集重复覆盖)
    stale_ids = [
        e.id
        for e in db.query(DramaEpisode).filter(DramaEpisode.project_id == project.id).all()
        if any(from_ch <= n <= to_ch for n in episode_source_chapters(e))
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
                source_chapters=spec["source_chapters"],
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


def _parse_source_chapters(item: dict, from_ch: int, to_ch: int) -> list[int]:
    """取这一集的源章号列表:兼容 source_chapters(新)与 source_chapter(旧/漏写),
    越界的章号丢弃,全丢光则回落到 from_ch(集照样能建,只是取文可能不准)。"""
    raw = item.get("source_chapters")
    if not isinstance(raw, list) or not raw:
        raw = [item.get("source_chapter")]
    nums: list[int] = []
    for v in raw:
        n = coerce_int(v, 0, lo=0)
        if from_ch <= n <= to_ch and n not in nums:
            nums.append(n)
    return sorted(nums) or [from_ch]


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
