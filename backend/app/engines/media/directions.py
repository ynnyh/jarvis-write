# app/engines/media/directions.py
# -*- coding: utf-8 -*-
"""三条出片线共用的画风方向目录(key 白名单 + 给 LLM 的方向硬约束 + 给用户的提示)。

原名 `DRAMA_DIRECTIONS`,长在 `drama/common.py` 里——但用它的不止漫剧:宣传片的
「画面方向」、情绪短片的「画风」选择、两条线的 meta 接口都从那儿取,名字里的 DRAMA
纯属误导。目录只此一份:加一档画风,三条线同时多一个选项,不用改三处。

真人写实保留但挂警示:AI 真人目前仍有恐怖谷痕迹,跨镜头一致性比动画系难得多。
"""
from __future__ import annotations

DIRECTIONS: list[dict] = [
    {"key": "auto", "label": "AI 按书定", "directive": "按本书类型自选最合适的动画系画风(默认排除真人写实)",
     "tip": ""},
    {"key": "comic_cn", "label": "国漫厚涂", "directive": "国漫厚涂插画风:笔触沉稳、块面光影、剧场感构图",
     "tip": ""},
    {"key": "anime_jp", "label": "日系二次元", "directive": "日式动画赛璐璐风:线条干净、平涂上色、动画角色面容",
     "tip": ""},
    {"key": "render3d", "label": "3D 动画", "directive": "三维动画渲染风:国创3D剧场感,材质与光感细腻",
     "tip": ""},
    {"key": "live", "label": "真人写实", "directive": "真人实拍质感:电影感打光、写实皮肤材质、浅景深",
     "tip": "AI 真人目前仍有恐怖谷痕迹,跨镜头一致性更难,建议先小范围试再铺量"},
    {"key": "ink_wash", "label": "水墨国风", "directive": "水墨画风:留白构图、墨色浓淡、写意笔触",
     "tip": ""},
    {"key": "cyber", "label": "赛博霓虹", "directive": "赛博朋克霓虹风:高饱和冷暖对比、夜景光污染、金属质感",
     "tip": ""},
]
_DIRECTION_MAP = {d["key"]: d for d in DIRECTIONS}
VALID_DIRECTIONS = tuple(_DIRECTION_MAP)


def direction_directive(key: str) -> str:
    """方向 key → 给 LLM 的硬约束文案;未知 key 回落到 auto。"""
    return (_DIRECTION_MAP.get(key) or _DIRECTION_MAP["auto"])["directive"]


def direction_label(key: str) -> str:
    return (_DIRECTION_MAP.get(key) or _DIRECTION_MAP["auto"])["label"]
