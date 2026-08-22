# app/engines/drama/storyboard.py
# -*- coding: utf-8 -*-
"""分镜表:剧本 lines → 镜头清单(场景/角色/景别/运镜/台词/时长)。

覆盖式重生成:重拆会删旧镜头。生成前 progress 报预估镜头数(成本透明,
借鉴 Toonflow)。景别/运镜收敛到白名单口径(宽容同义词截短保留)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import coerce_int, episode_dict, shot_dict
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import STORYBOARD_PROMPT

_MAX_SHOTS = 24


class DramaStoryboardError(ValueError):
    """分镜生成的业务性错误(信息直接上屏)。"""


def _norm_shot_type(raw: object) -> str:
    v = str(raw or "").strip()
    return v[:20]


def _norm_camera(raw: object) -> str:
    v = str(raw or "").strip()
    return v[:20]


async def build_storyboard(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    script = episode.script or {}
    lines = script.get("lines") if isinstance(script, dict) else None
    if not lines:
        raise DramaStoryboardError("这一集还没有剧本,先「写剧本」再拆分镜。")

    scene_names = [
        n
        for (n,) in db.query(DramaSceneCard.name)
        .filter(DramaSceneCard.project_id == project.id)
        .all()
    ]
    character_names = [
        n
        for (n,) in db.query(DramaCharacterCard.name)
        .filter(DramaCharacterCard.project_id == project.id)
        .all()
    ]

    lines_block = "\n".join(
        f"  {i}. {l.get('speaker', '')}: {l.get('text', '')}"
        + (f"  (画面:{l.get('action')})" if l.get("action") else "")
        for i, l in enumerate(lines, start=1)
        if isinstance(l, dict)
    )
    estimate = max(6, round(episode.duration_target_s / 4))
    progress(f"AI 正在拆分镜(预计 {estimate} 格左右,覆盖旧分镜)…")

    adapter = get_adapter_for(Task.DRAMA_STORYBOARD, timeout=300)
    prompt = STORYBOARD_PROMPT.format(
        ep_index=episode.ep_index,
        ep_title=episode.title,
        duration_target_s=episode.duration_target_s,
        scene_names="、".join(scene_names) or "(暂无场景卡,按剧本自拟简短场景名)",
        character_names="、".join(character_names) or "(暂无角色卡,按剧本人名)",
        lines_block=lines_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    shots_out: list[dict] = []
    for item in (data.get("shots") or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        characters = [
            str(c).strip() for c in (item.get("characters") or []) if str(c or "").strip()
        ][:6]
        shots_out.append(
            {
                "scene_name": str(item.get("scene_name") or "").strip()[:200],
                "characters": characters,
                "action_desc": action[:300],
                "shot_type": _norm_shot_type(item.get("shot_type")),
                "camera": _norm_camera(item.get("camera")),
                "dialogue": str(item.get("dialogue") or "").strip()[:400],
                "duration_s": coerce_int(item.get("duration_s"), 4, lo=1, hi=10),
            }
        )
        if len(shots_out) >= _MAX_SHOTS:
            break
    if not shots_out:
        raise DramaStoryboardError("分镜结果为空,请重试(或先重写剧本)。")

    db.query(DramaShot).filter(DramaShot.episode_id == episode.id).delete(
        synchronize_session=False
    )
    for i, spec in enumerate(shots_out, start=1):
        db.add(DramaShot(episode_id=episode.id, seq=i, **spec))
    episode.status = "storyboarded"
    db.commit()

    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.seq)
        .all()
    )
    return {"episode": episode_dict(episode), "shots": [shot_dict(s) for s in shots]}
