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
    # Q版沙雕漫画(条漫/表情包系):大头小身+粗描边+平涂,搞笑日常段子的主力画风
    {"key": "chibi", "label": "Q版沙雕漫画",
     "directive": "Q版二头身漫画风:大头小身(头占身高一半以上)、粗黑描边、平涂上色带软渐变,"
                  "表情包式夸张表情(瞪圆眼/冒汗/张嘴大喊/脸红),条漫插画质感,"
                  "背景做简化处理不抢戏;严禁真人比例与写实质感",
     "tip": "搞笑日常/段子类最搭;对话框与台词字幕建议剪辑时再压,画面里别生成文字"},
    # 情绪短片特调的五档(手作/怀旧系与情绪命题天然亲;三条线共用,增量无破坏)
    {"key": "watercolor", "label": "手绘水彩绘本", "directive": "手绘水彩绘本风:纸纹底、透明叠色、淡彩留白,温柔手作感",
     "tip": "治愈/思念/童趣类命题最搭"},
    {"key": "crayon", "label": "蜡笔涂鸦", "directive": "蜡笔涂鸦风:粗粝笔触、儿童画配色、歪歪扭扭的线条,天真笨拙感",
     "tip": "童趣/回忆视角好使"},
    {"key": "papercut", "label": "剪纸拼贴", "directive": "剪纸拼贴风:层叠纸片、硬边剪影、手工质感,舞台式空间",
     "tip": ""},
    {"key": "claymation", "label": "黏土定格", "directive": "黏土定格动画风:柔软塑形质感、指纹痕迹、微缩场景、逐帧手感",
     "tip": ""},
    {"key": "film_grain", "label": "胶片怀旧颗粒", "directive": "胶片怀旧风:35mm 颗粒、褪色偏色、漏光与划痕、旧照片般的温润",
     "tip": "回忆/遗憾类命题氛围直接拉满"},
]
_DIRECTION_MAP = {d["key"]: d for d in DIRECTIONS}
VALID_DIRECTIONS = tuple(_DIRECTION_MAP)


def direction_directive(key: str) -> str:
    """方向 key → 给 LLM 的硬约束文案;未知 key 回落到 auto。"""
    return (_DIRECTION_MAP.get(key) or _DIRECTION_MAP["auto"])["directive"]


def direction_label(key: str) -> str:
    return (_DIRECTION_MAP.get(key) or _DIRECTION_MAP["auto"])["label"]
