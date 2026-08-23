# app/engines/drama/storyboard.py
# -*- coding: utf-8 -*-
"""分镜表:剧本 lines → 镜头清单(场景/角色/景别/运镜/台词/时长)。

覆盖式重生成:重拆会删旧镜头。生成前 progress 报预估镜头数(成本透明,
借鉴 Toonflow)。景别/运镜收敛到白名单口径(宽容同义词截短保留)。

镜头数上限按本集目标时长算(每格最短 2 秒),不写死——写死 24 格时
180 秒的长集会被砍成 ~96 秒的成片,且用户看不到任何提示。真被截断/
总时长明显短于目标时,返回 notice 让前端如实显示。
"""
from __future__ import annotations

from math import ceil

from sqlalchemy.orm import Session

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    Project,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import coerce_int, episode_dict, shots_payload
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import STORYBOARD_PROMPT

# 镜头数上限:按「每格最短 2 秒」算够不够铺满目标时长,再夹在 [8, 80] 内
# (下限保证短集也有基本镜头量,上限纯防 LLM 抽风吐几百格)
_SEC_PER_SHOT_MIN = 2
_CAP_FLOOR = 8
_CAP_CEIL = 80
# 分镜总时长低于目标的这个比例,就提示「短了」(留一点正常的取舍余量)
_SHORT_RATIO = 0.8


def shot_cap(duration_target_s: int) -> int:
    """本集允许的最大镜头数(按目标时长动态算)。"""
    need = ceil(max(0, duration_target_s) / _SEC_PER_SHOT_MIN)
    return max(_CAP_FLOOR, min(_CAP_CEIL, need))


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
    cap = shot_cap(episode.duration_target_s)
    progress(f"AI 正在拆分镜(预计 {estimate} 格左右,上限 {cap} 格,覆盖旧分镜)…")

    adapter = get_adapter_for(Task.DRAMA_STORYBOARD, timeout=300)
    prompt = STORYBOARD_PROMPT.format(
        ep_index=episode.ep_index,
        ep_title=episode.title,
        duration_target_s=episode.duration_target_s,
        max_shots=cap,
        scene_names="、".join(scene_names) or "(暂无场景卡,按剧本自拟简短场景名)",
        character_names="、".join(character_names) or "(暂无角色卡,按剧本人名)",
        lines_block=lines_block,
    )
    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)

    shots_out: list[dict] = []
    dropped = 0
    for item in (data.get("shots") or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        if len(shots_out) >= cap:
            dropped += 1  # 超上限:记下来如实告诉用户,不静默丢
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
    total_s = sum(s.duration_s for s in shots)
    notes: list[str] = []
    if dropped:
        notes.append(
            f"AI 多给了 {dropped} 格,已按上限 {cap} 格截断"
            f"(上限按目标 {episode.duration_target_s} 秒算)"
        )
    if total_s < episode.duration_target_s * _SHORT_RATIO:
        notes.append(
            f"分镜总时长 {total_s} 秒,短于目标 {episode.duration_target_s} 秒——"
            "可「重新拆分镜」,或手动加格/调单格时长"
        )
    return {
        "episode": episode_dict(episode),
        "shots": shots_payload(db, project.id, shots),
        "truncated": dropped > 0,
        "notice": ";".join(notes),
    }
