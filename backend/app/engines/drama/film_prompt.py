# app/engines/drama/film_prompt.py
# -*- coding: utf-8 -*-
"""漫剧整片提示词(分段版):分镜 + 角色卡 + 画风卡 → 按段切好的成片提示词文档。

外部视频模型单次最多生成 15s(少数 30s),而一集 60-180s——所以产出的不是一条
整片提示词,而是按镜头边界贪心切好的 N 段各自独立可用的提示词文档:每段单独贴
进模型都能生成风格与人物一致的片段(段首逐字复述画风锚,涉及角色逐字融入外貌
服饰),全部生成后按段号拼接。台词原文嵌入并按剧本 lines 反查说话人。

与三轨提示词的分工:那份逐格喂即梦/可灵出图出片、人再拼;这份是外部端到端
模型的成品稿。生成即整体覆盖 episode.film_prompt;手改/整段粘贴也存同一列。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaShot,
    DramaStyleCard,
    Project,
)
from app.engines.media.segments import group_by_limit
from app.engines.media.text import speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.film_prompt import (
    DRAMA_FRAMING,
    SEGMENTED_FILM_PROMPT_TEMPLATE,
    VALID_SEGMENT_S,
    segmented_doc_header,
)


class FilmPromptError(ValueError):
    """整片提示词生成的业务性错误(信息直接上屏)。"""


def _characters_block(db: Session, project_id: int, shots: list[DramaShot]) -> str:
    """出场角色档案(按首次出场排序):外貌/服饰/声线,生成时逐字融入涉及段。"""
    names: list[str] = []
    for shot in shots:
        for name in shot.characters or []:
            if name and name not in names:
                names.append(name)
    if not names:
        return "(本集分镜没有标注出场角色:若有固定人物,外貌/服饰自行定死并全片一致)"
    cards = {
        c.name: c
        for c in db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .all()
    }
    rows = []
    for name in names:
        c = cards.get(name)
        if c is None:
            rows.append(f"- {name}(没有角色卡:外貌/服饰按剧情自行补写,并保持全片一致)")
            continue
        gender = {"female": "女", "male": "男"}.get(c.gender, "")
        rows.append(
            f"- {name}" + (f"({gender})" if gender else "")
            + f"|外貌:{c.appearance_cn or '未定'}"
            + f"|服饰:{c.outfit_cn or '未定'}"
            + f"|声线:{c.voice_desc or '未定'}"
        )
    return "\n".join(rows)


def _segments_block(groups: list[list[DramaShot]], lines: list) -> str:
    """分段计划原料:每段的镜头行 + 该段覆盖的台词(带说话人,按剧本 lines 反查)。"""
    rows = []
    t = 0
    for i, group in enumerate(groups, 1):
        start, end = t, t + sum(int(s.duration_s or 0) for s in group)
        t = end
        seg_rows = [
            f"  - 镜头{s.seq}|场景:{s.scene_name or '未标'}|{s.shot_type or '中景'}"
            f"|运镜:{s.camera or '固定'}|{s.duration_s}秒"
            f"|角色:{('、'.join(s.characters) or '无')}"
            f"|画面:{(s.action_desc or '').strip() or '未写'}"
            for s in group
        ]
        block = f"【第{i}段|{start}—{end}秒】\n" + "\n".join(seg_rows)
        for s in group:
            d = (s.dialogue or "").strip()
            if not d:
                continue
            sp = speaker_of(d, lines)
            block += f"\n  台词{f'({sp})' if sp else ''}:{d}"
        rows.append(block)
    return "\n".join(rows)


async def build_episode_film_prompt(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None,
    segment_s: int = 15,
) -> dict:
    """按段组装生成整片提示词文档,整体覆盖 episode.film_prompt。返回字数与段数。"""
    if segment_s not in VALID_SEGMENT_S:
        raise FilmPromptError("单段时长只支持 15 / 30 秒。")

    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.seq)
        .all()
    )
    if not shots:
        raise FilmPromptError("这一集还没有分镜,先「④-2 拆分镜」再生成整片提示词。")

    style = (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project.id)
        .first()
    )
    lines = (episode.script or {}).get("lines") or []
    groups = group_by_limit(shots, segment_s)
    total_s = sum(int(s.duration_s or 0) for s in shots)

    extra = ""
    if episode.hook:
        extra += f"【本集开场钩子】{episode.hook}\n"
    if episode.cliffhanger:
        extra += f"【结尾卡点(最后一段要接住它)】{episode.cliffhanger}\n"

    progress(f"AI 正在把 {len(groups)} 段分镜组装成分段提示词…")
    adapter = get_adapter_for(Task.DRAMA_PROMPT, timeout=300)
    prompt = SEGMENTED_FILM_PROMPT_TEMPLATE.format(
        workshop_label=f"漫剧第 {episode.ep_index} 集《{episode.title}》",
        title_line=f"《{project.title}》第 {episode.ep_index} 集",
        total_s=total_s,
        seg_count=len(groups),
        segment_s=segment_s,
        ratio=(style.ratio if style else "") or "9:16",
        framing=DRAMA_FRAMING,
        style_block=(style.style_cn if style else "") or "(未定画风,按剧情题材自行设定视觉质感)",
        extra_blocks=extra,
        characters_block=_characters_block(db, project.id, shots),
        segments_block=_segments_block(groups, lines),
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise FilmPromptError("模型返回了空内容,请重试一次。")
    episode.film_prompt = segmented_doc_header(len(groups), segment_s) + text
    db.commit()
    return {"chars": len(episode.film_prompt), "segments": len(groups)}
