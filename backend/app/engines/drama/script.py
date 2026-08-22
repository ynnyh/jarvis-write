# app/engines/drama/script.py
# -*- coding: utf-8 -*-
"""单集剧本:按集规划 + 源章节正文 + 角色卡声线,写出可拆分镜的台词稿。

对白演绎(dialogue)为主模式:角色台词驱动;口播解说(narration):旁白为主。
重写剧本会把状态拉回 scripted(旧分镜如仍在,前端提示已过期)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaCharacterCard, DramaEpisode, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import (
    MODE_DESC,
    chapter_final_text,
    clip,
    episode_dict,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import EPISODE_SCRIPT_PROMPT

# 源章节正文注入上限(字符):剧本只需要主体情节,超长正文截断防提示词爆炸
_MAX_CHAPTER_CHARS = 6000
_MAX_LINES = 40


class DramaScriptError(ValueError):
    """剧本生成的业务性错误(信息直接上屏)。"""


def _characters_block(db: Session, project_id: int) -> str:
    """角色速览:名字 + 声线 + 标志服饰(台词口感与辨识度用)。"""
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .order_by(DramaCharacterCard.id)
        .all()
    )
    if not cards:
        return ""
    lines = [
        f"  {c.name}|声线:{c.voice_desc or '未定'}|标志:{c.outfit_cn or '未定'}"
        for c in cards
    ]
    return "【角色速览(台词口感参考)】\n" + "\n".join(lines) + "\n"


def _prev_block(db: Session, project_id: int, ep_index: int) -> str:
    """上一集结尾卡点:保证集间衔接不断档。"""
    prev = (
        db.query(DramaEpisode.cliffhanger, DramaEpisode.title)
        .filter(
            DramaEpisode.project_id == project_id,
            DramaEpisode.ep_index == ep_index - 1,
        )
        .first()
    )
    if prev and prev.cliffhanger:
        return f"【上一集结尾卡点(开场要承接)】{prev.cliffhanger}\n"
    return ""


async def write_episode_script(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    text = chapter_final_text(db, project.id, episode.source_chapter)
    if not text:
        raise DramaScriptError(
            f"第 {episode.source_chapter} 章没有正文,先在写作区生成并定稿该章。"
        )

    progress(f"AI 正在写第 {episode.ep_index} 集剧本({episode.mode})…")
    adapter = get_adapter_for(Task.DRAMA_SCRIPT, timeout=300)
    prompt = EPISODE_SCRIPT_PROMPT.format(
        title=project.title,
        ep_index=episode.ep_index,
        ep_title=episode.title,
        mode_desc=MODE_DESC.get(episode.mode, MODE_DESC["dialogue"]),
        duration_target_s=episode.duration_target_s,
        hook=episode.hook or "(规划未给,自行设计强钩子开场)",
        recap=episode.recap,
        cliffhanger=episode.cliffhanger or "(规划未给,自行设计卡点结尾)",
        prev_block=_prev_block(db, project.id, episode.ep_index),
        characters_block=_characters_block(db, project.id),
        source_chapter=episode.source_chapter,
        chapter_text=text[:_MAX_CHAPTER_CHARS],
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    lines_out: list[dict] = []
    for item in (data.get("lines") or []):
        if not isinstance(item, dict):
            continue
        text_line = clip(item.get("text"), 300)
        if not text_line:
            continue
        lines_out.append(
            {
                "speaker": clip(item.get("speaker"), 60) or "旁白",
                "text": text_line,
                "action": clip(item.get("action"), 120),
            }
        )
        if len(lines_out) >= _MAX_LINES:
            break
    if not lines_out:
        raise DramaScriptError("剧本结果为空,请重试。")

    episode.script = {
        "mode": episode.mode,
        "synopsis": clip(data.get("synopsis"), 300),
        "lines": lines_out,
    }
    episode.status = "scripted"
    db.commit()
    return episode_dict(episode)
