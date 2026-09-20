# app/engines/anime/__init__.py
# -*- coding: utf-8 -*-
"""动画短剧引擎:固定卡司的原创系列动画,按集出梗纲三选一/分镜/整集分段提示词。"""
from app.engines.anime.common import (
    MAX_SHOTS,
    PREMISE_MAX,
    TITLE_MAX,
    VALID_EPISODE_S,
    AnimeError,
    episode_dict,
    genre_of,
    norm_cast,
    series_dict,
    valid_genres,
)
from app.engines.anime.episodes import (
    anime_chat,
    build_film_prompt,
    confirm_synopsis,
    gen_shots,
    gen_takes,
    generate_cast,
    pick_take,
    save_cast,
    save_shots,
)

__all__ = [
    "AnimeError",
    "MAX_SHOTS",
    "PREMISE_MAX",
    "TITLE_MAX",
    "VALID_EPISODE_S",
    "anime_chat",
    "build_film_prompt",
    "confirm_synopsis",
    "episode_dict",
    "episode_dict",
    "gen_shots",
    "gen_takes",
    "genre_of",
    "generate_cast",
    "norm_cast",
    "pick_take",
    "save_cast",
    "save_shots",
    "series_dict",
    "valid_genres",
]
