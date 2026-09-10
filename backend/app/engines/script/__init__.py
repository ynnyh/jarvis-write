# app/engines/script/__init__.py
# -*- coding: utf-8 -*-
"""剧本工坊引擎:分集大纲、单集生成(格式门禁 + 集末交接契约)、小说改编素材。

2026-09-10 从 app/api/scripts.py 下沉。路由只管鉴权、会话与状态码,
生成编排与质量件归这里。
"""
from app.engines.script.common import (
    STATUS_CN,
    end_state_of,
    episode_dict,
    find_version,
    push_version,
    version_list,
)
from app.engines.script.generate import (
    ScriptError,
    generate_episode,
    generate_outline,
)

__all__ = [
    "STATUS_CN",
    "ScriptError",
    "end_state_of",
    "episode_dict",
    "find_version",
    "generate_episode",
    "generate_outline",
    "push_version",
    "version_list",
]
