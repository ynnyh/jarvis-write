# app/engines/drama/film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词:分镜 + 角色卡 + 画风卡 → 一条「一次生成一整片」的成片提示词。

给端到端音频原生视频模型(Sora/Veo/可灵这类)用:一条提示词直接出带对白口型
同步的整集。与三轨提示词的分工:那份逐格喂即梦/可灵出图出片、人再拼;这份整段
贴进端到端模型一次出片。生成即整体覆盖 ep.film_prompt;用户手改/整段粘贴自己
写的版本也存同一列——「生成即覆盖,保存即替换」,不存历史。
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
from app.engines.media.text import speaker_of, strip_fences
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import FILM_PROMPT_PROMPT


class FilmPromptError(ValueError):
    """整片提示词生成的业务性错误(信息直接上屏)。"""


def _characters_block(db: Session, project_id: int, shots: list[DramaShot]) -> str:
    """出场角色档案(按首次出场排序):外貌/服饰/声线,生成时逐字融入一致性段。"""
    names: list[str] = []
    for shot in shots:
        for name in shot.characters or []:
            if name and name not in names:
                names.append(name)
    if not names:
        return "(本集分镜没有标注出场角色)"
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


def _shots_block(episode: DramaEpisode, shots: list[DramaShot]) -> str:
    """分镜清单:时间码由模型按时长累计,这里给全原料(含台词说话人反查)。"""
    lines = (episode.script or {}).get("lines") or []
    rows = []
    for s in shots:
        dialogue = (s.dialogue or "").strip()
        speaker = speaker_of(dialogue, lines)
        row = (
            f"- 镜头{s.seq}|场景:{s.scene_name or '未标'}|{s.shot_type or '中景'}"
            f"|运镜:{s.camera or '固定'}|{s.duration_s}秒"
            f"|角色:{('、'.join(s.characters) or '无')}"
            f"|画面:{(s.action_desc or '').strip() or '未写'}"
        )
        if dialogue:
            row += f"|台词:{f'{speaker}:' if speaker else ''}{dialogue}"
        rows.append(row)
    return "\n".join(rows)


def _strip_fences(text: str) -> str:
    """剥掉模型偶尔裹上来的 markdown 围栏——上屏的就是纯提示词,不带这些赘余。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


async def build_episode_film_prompt(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    """组装生成整片提示词,整体覆盖 ep.film_prompt。返回字数供 job 结果展示。"""
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
    progress("AI 正在把分镜组装成整片提示词…")
    adapter = get_adapter_for(Task.DRAMA_PROMPT, timeout=300)
    prompt = FILM_PROMPT_PROMPT.format(
        title=project.title,
        ep_index=episode.ep_index,
        ep_title=episode.title,
        ratio=(style.ratio if style else "") or "9:16",
        total_s=sum(s.duration_s or 0 for s in shots),
        style_cn=(style.style_cn if style else "") or "(未定画风,按剧情题材自行设定视觉质感)",
        hook=episode.hook or "(无)",
        recap=episode.recap or "(无)",
        cliffhanger=episode.cliffhanger or "(无)",
        characters_block=_characters_block(db, project.id, shots),
        shots_block=_shots_block(episode, shots),
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise FilmPromptError("模型返回了空内容,请重试一次。")
    episode.film_prompt = text
    db.commit()
    return {"chars": len(text)}
