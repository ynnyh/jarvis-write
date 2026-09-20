# app/engines/anime/common.py
# -*- coding: utf-8 -*-
"""动画短剧共用口径:类型目录、档位、上限、序列化、卡司归一。

产品决策(用户拍板):
- 类型用户自选(目录下发),爆笑虫子只是格式参考不是硬编码品类;
- 每集 60/90 秒两档;台词动作全开(音频原生模型直接生成语音与动作);
- 卡司是系列级资产:恰好 1 主角,配角 0-3;locked 角色批量重出不覆盖。
"""
from __future__ import annotations

from typing import Any

from app.prompts.anime import ANIME_GENRES, genre_of

TITLE_MAX = 120      # 系列名
PREMISE_MAX = 500    # 一句话设定 / 单集命题
EP_TITLE_MAX = 60    # 集标题
CATCH_MAX = 60       # 口头禅
VALID_EPISODE_S = (60, 90)
VALID_SEGMENT_S = (15, 30)   # 整集提示词的单段上限(外部模型现实上限)
MAX_CAST = 4                 # 1 主角 + 至多 3 配角
MAX_SHOTS = 40               # 单集分镜上限(90s ÷ 2s 也不该到 40,防跑飞)

STATUS_CN = {
    "cast_empty": "待定卡司", "cast_ready": "卡司就绪", "active": "连载中",
    "premise": "待聊简介", "takes_ready": "梗纲已出", "synopsis_ready": "简介已确认",
    "shots_ready": "分镜已出", "prompted": "提示词已出",
}


class AnimeError(ValueError):
    """动画短剧的业务性错误(信息直接上屏)。"""


def valid_genres() -> list[str]:
    return [g["key"] for g in ANIME_GENRES]


__all__ = [
    "ANIME_GENRES", "AnimeError", "CATCH_MAX", "EP_TITLE_MAX", "MAX_CAST",
    "MAX_SHOTS", "PREMISE_MAX", "STATUS_CN", "TITLE_MAX", "VALID_EPISODE_S",
    "VALID_SEGMENT_S", "genre_of", "valid_genres",
]


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def norm_cast(cast: Any) -> list[dict]:
    """卡司归一:恰好 1 主角;字段裁剪;丢无名空壳;最多 MAX_CAST 个。

    API 手改保存与 AI 生成落库都走这里,库里永远存归一后的形状。
    """
    if not isinstance(cast, list):
        raise AnimeError("卡司格式不对:应该是一个角色数组。")
    out: list[dict] = []
    for c in cast[:MAX_CAST + 2]:
        if not isinstance(c, dict):
            continue
        name = _clean(c.get("name"), 30)
        if not name:
            continue
        out.append({
            "name": name,
            "role": "主角" if str(c.get("role") or "").strip() == "主角" else "配角",
            "appearance": _clean(c.get("appearance"), 800),
            "wardrobe": _clean(c.get("wardrobe"), 300),
            "personality": _clean(c.get("personality"), 300),
            "catchphrase": _clean(c.get("catchphrase"), CATCH_MAX),
            "locked": bool(c.get("locked")),
        })
    if not out:
        raise AnimeError("卡司里一个有名字的角色都没有:至少要 1 个主角。")
    # 主角恰好 1 个:第一个主角之外的「主角」降级为配角;一个都没有则提拔第一个
    seen_hero = False
    for c in out:
        if c["role"] == "主角":
            if seen_hero:
                c["role"] = "配角"
            else:
                seen_hero = True
    if not seen_hero:
        out[0]["role"] = "主角"
    return out[:MAX_CAST]


def cast_block(cast: list[dict]) -> str:
    """卡司 → 提示词原料块:定妆是跨集一致性的锚,生成时逐字注入。"""
    rows = []
    for c in cast:
        line = (f"- {c['name']}(主角)" if c["role"] == "主角" else f"- {c['name']}(配角)")
        line += f" 定妆:{c['appearance'] or '(未写,由你按设定补齐并全片一致)'}"
        if c["wardrobe"]:
            line += f" 服装:{c['wardrobe']}"
        if c["personality"]:
            line += f" 性格:{c['personality']}"
        if c["catchphrase"]:
            line += f" 口头禅:「{c['catchphrase']}」"
        rows.append(line)
    return "\n".join(rows)


def merge_cast_locked(old: list[dict], new: list[dict]) -> list[dict]:
    """重出卡司时 locked 角色原样保留(按名字),其余用新提案;locked 主角优先。"""
    locked_names = {c["name"] for c in old if c.get("locked")}
    kept = [c for c in old if c.get("locked")]
    kept_names = {c["name"] for c in kept}
    fresh = [c for c in new if c["name"] not in kept_names]
    # locked 里有主角就不再要新主角(把新提案的主角降级),保证全组恰好 1 主角
    if any(c["role"] == "主角" for c in kept):
        for c in fresh:
            if c["role"] == "主角":
                c["role"] = "配角"
    merged = kept + fresh
    # 去重(同名取新)并归一
    seen: set[str] = set()
    dedup: list[dict] = []
    for c in merged:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        dedup.append(c)
    return norm_cast(dedup)


def series_dict(row) -> dict:
    return {
        "id": row.id, "title": row.title, "premise": row.premise,
        "genre": row.genre, "genre_label": genre_of(row.genre)["label"],
        "direction": row.direction, "style_cn": row.style_cn,
        "cast": list(row.cast or []), "episode_s": row.episode_s,
        "status": row.status,
    }


def episode_dict(row) -> dict:
    return {
        "id": row.id, "series_id": row.series_id, "seq": row.seq,
        "title": row.title, "premise": row.premise,
        "chat": list(row.chat or []),
        "synopsis": row.synopsis or "", "synopsis_ok": bool(row.synopsis_ok),
        "takes": list(row.takes or []), "chosen": row.chosen,
        "shots": list(row.shots or []), "film_prompt": row.film_prompt or "",
        "status": row.status,
    }
