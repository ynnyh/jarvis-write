# app/engines/series/__init__.py
"""角色系列短片引擎:固定主角的 5-15 秒系列短视频,主角档案驱动、轻量单条直出。"""
from .common import (
    BRIEF_MAX,
    HINTS_MAX,
    LOOK_MAX,
    MAX_DURATION_S,
    MIN_DURATION_S,
    NAME_MAX,
    NEGATIVE_MAX,
    PLOT_MAX,
    PROMPT_MAX,
    STATUS_CN,
    TITLE_MAX,
    character_dict,
    episode_dict,
    norm_output,
)
from .generate import SeriesError, draft_look, generate_episode

__all__ = [
    "SeriesError",
    "draft_look",
    "generate_episode",
    "character_dict",
    "episode_dict",
    "norm_output",
    "MIN_DURATION_S",
    "MAX_DURATION_S",
    "NAME_MAX",
    "BRIEF_MAX",
    "LOOK_MAX",
    "PLOT_MAX",
    "TITLE_MAX",
    "PROMPT_MAX",
    "NEGATIVE_MAX",
    "HINTS_MAX",
    "STATUS_CN",
]
