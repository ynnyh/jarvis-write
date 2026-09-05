# app/engines/clips/common.py
# -*- coding: utf-8 -*-
"""情绪短片引擎公共件:主题/导向维度目录、序列化、字典版切段(时间轴口径同 media.subtitles)。"""
from __future__ import annotations

from app.db.models import MoodClip
from app.engines.media.directions import direction_directive, direction_label
from app.engines.media.segments import plan_chunks
from app.engines.media.text import coerce_int
from app.prompts.clips import (
    dialogue_style_rule,
    intensity_rule,
    pacing_rule,
)

# 情绪主题目录:key 白名单 + 给提示词的「导演提示」(这个情绪怎么拍才戳)
CLIP_THEMES: list[dict] = [
    {"key": "regret", "label": "遗憾", "directive": "错过与未说出口;克制冷藏,最后一格才许破防"},
    {"key": "quarrel", "label": "争吵", "directive": "越吵越近或越吵越远;台词短促有火药味,收在突然的安静"},
    {"key": "love", "label": "爱情", "directive": "具体的爱不是形容词,是动作与细节;避免俗套桥段堆砌"},
    {"key": "childlike", "label": "童趣", "directive": "儿童视角的认真与荒唐;大人世界作背景板,笑点干净"},
    {"key": "missing", "label": "思念", "directive": "不在场的人靠在场之物活着;物件特写是主语"},
    {"key": "lonely", "label": "孤独", "directive": "人群里的孤独比荒野狠;空间大、人小、声音少"},
    {"key": "healing", "label": "治愈", "directive": "小而确定的暖;不煽情,收在释然的一口气"},
    {"key": "hotblood", "label": "热血", "directive": "憋到顶再放;节奏前压后爆,收在起跑/出拳那一帧"},
    {"key": "farewell", "label": "告别", "directive": "告别都不说破;最后一次寻常对话就是告别本身"},
    {"key": "reunion", "label": "重逢", "directive": "千言万语堵在一声称呼上;迟疑的肢体先于语言"},
]
_THEME_MAP = {t["key"]: t for t in CLIP_THEMES}
VALID_THEMES = tuple(_THEME_MAP)

# 灵感工坊玩法目录:key 白名单 + 「导演提示」(含强画风锚的风格化措辞——避免直接点名
# 有版权的品牌/IP 名,改成描述画风质感,模型更稳也不会往临幕原片跑)。
# group = 视觉气质分组(前端「先选大方向再抽卡」的第一级,见 CLIPS_PLAY_GROUPS)。
CLIPS_PLAYS: list[dict] = [
    {"key": "ghibli", "label": "治愈手绘", "group": "warm", "directive": "治愈系手绘动画质感:手绘动画笔触、明快配色(透明天空蓝/云白/草地绿),飞行与被风推动的意象;2D 细腻帧画,线条干净圆润"},
    {"key": "clay", "label": "黏土定格", "group": "retro", "directive": "黏土/布偶定格动画质感:手作感、忽快忽慢的定格停顿、表面能看到指纹与棉絮,光顺暖;玩偶表情夸张呆萌"},
    {"key": "cyber", "label": "赛博雨夜", "group": "urban", "directive": "赛博朋克霓虹都市雨夜:青紫粉撞色霓虹、雨线、逆光剪影、玻璃反光与积水倒影;夜景高对比、高饱和"},
    {"key": "pixel", "label": "像素复古", "group": "urban", "directive": "8-bit/16-bit 像素游戏质感、复古 CRT 色调、粒子噪点与扫描线,卷轴式镜头,角色像素小人穿插"},
    {"key": "hk", "label": "港风胶片", "group": "retro", "directive": "老港片胶片质感:暖黄+青色调、手持画面微晃、胶片颗粒,街市霓虹招牌与烟火气,高饱和低密度"},
    {"key": "bw", "label": "黑白胶片", "group": "retro", "directive": "黑白纪实摄影/胶片:颗粒与灰阶层次、强侧光、去彩色,靠明暗与构图说话,人物剪影分明"},
    {"key": "watercolor", "label": "水彩绘本", "group": "warm", "directive": "手绘水彩绘本质感:纸纹、晕染、留白、通透浅色调,边缘柔和,像被一页一页翻开的绘本"},
    {"key": "papercut", "label": "剪纸皮影", "group": "retro", "directive": "剪纸/皮影戏质感:平面多层剪纸、镂空纹理、暖色背光,光影边界清晰,有民间工艺的装饰味"},
    {"key": "miniature", "label": "微缩玩具", "group": "warm", "directive": "微缩模型/玩具屋视角:极浅景深、逼真材质但物件尺寸像手办,人物像小玩具,带摆拍的趣味"},
    {"key": "timelapse", "label": "延时奇观", "group": "urban", "directive": "延时摄影质感:天空云层与城市光线推移、宏大空镜、稳定机位,时间被压缩的流动感"},
    {"key": "animal", "label": "动物拟人", "group": "whim", "directive": "动物拟人日常:穿人类衣物的卡通动物、有着人类的生活场景,温馨自然、自带反差萌"},
    {"key": "nonsense", "label": "荒诞脑洞", "group": "whim", "directive": "荒诞即兴/黑色幽默:普通日常场景里塞一件极不合理的事,反差爽感、一本正经地荒谬"},
]
_PLAY_MAP = {t["key"]: t for t in CLIPS_PLAYS}
VALID_PLAYS = tuple(_PLAY_MAP)

# 玩法的视觉气质分组(「先选大方向」第一级)。展示目录,不进白名单校验。
CLIPS_PLAY_GROUPS: list[dict] = [
    {"key": "warm", "label": "温暖治愈", "desc": "看得心里发软的柔和质感:手绘、绘本、玩具屋"},
    {"key": "retro", "label": "复古光影", "desc": "胶片、纸张与手作的光:港片、黑白、剪纸、黏土"},
    {"key": "urban", "label": "都市奇观", "desc": "霓虹、像素与时间的流动:赛博、游戏、延时"},
    {"key": "whim", "label": "脑洞反差", "desc": "一本正经地离谱,自带反差萌"},
]
# free=故事工坊(自由创作):用户自带完整点子,引擎照点子拍;mood/play 是「命题驱动」
VALID_MODES = ("mood", "play", "free")

VALID_DURATIONS = (15, 30)

# ---- 导向维度(用户可细化的"方向"):目录 key 白名单 + label(前端展示) ----
# directive 进提示词的部分由 prompts.clips 的 *_rule 函数给(硬约束文案)。
DIALOGUE_STYLES: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "voiceover", "label": "旁白独白"},
    {"key": "dialogue", "label": "对白主导"},
    {"key": "silent", "label": "无台词"},
]
PACINGS: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "hook_first", "label": "爆点前置"},
    {"key": "slow_burn", "label": "层层蓄势"},
    {"key": "twist_end", "label": "结尾反转"},
]
INTENSITIES: list[dict] = [
    {"key": "auto", "label": "AI 定"},
    {"key": "restrained", "label": "克制留白"},
    {"key": "standard", "label": "标准"},
    {"key": "intense", "label": "浓烈直给"},
]
VALID_DIALOGUE_STYLES = tuple(t["key"] for t in DIALOGUE_STYLES)
VALID_PACINGS = tuple(t["key"] for t in PACINGS)
VALID_INTENSITIES = tuple(t["key"] for t in INTENSITIES)


def _clip_mode(clip: MoodClip) -> str:
    """工坊类型:mood=情绪短片,play=灵感玩法,free=故事工坊;存量行(None)按 mood 兜底。"""
    return getattr(clip, "mode", "mood") or "mood"


def theme_label(clip: MoodClip) -> str:
    """主题显示:目录 key → 标签+导演提示;自定义 → 原文 + 兜底导演提示
    (自定义主题没有目录 directive 可靠,不补一句"落地成具体场景"就常拍成抽象意象)。

    按工坊类型分流:故事工坊(mode=free)以用户点子为主轴;灵感工坊(mode=play)
    查玩法目录,玩法 directive 含强画风锁定。"""
    if _clip_mode(clip) == "free":
        # 点子本身的"忠实还原"约束由 CLIPS_FREE_CONTEXT 模板负责,这里只给短标签
        return f"{clip.custom_theme.strip()}(自由创作)"
    if _clip_mode(clip) == "play":
        if clip.theme in _PLAY_MAP:
            p = _PLAY_MAP[clip.theme]
            return f"{p['label']}({p['directive']})"
        custom = clip.custom_theme.strip()
        if not custom:
            return "(未定)"
        return (
            f"{custom}(自定义灵感玩法:锁定画风质感与玩法,拍出辨识度,"
            "可以猎奇、可以有反差,别拍成平铺直叙)"
        )
    if clip.theme in _THEME_MAP:
        t = _THEME_MAP[clip.theme]
        return f"{t['label']}({t['directive']})"
    custom = clip.custom_theme.strip()
    if not custom:
        return "(未定)"
    return f"{custom}(自由命题:落地成具体场景与人物关系,别拍成抽象意象)"


def steering_block(clip: MoodClip) -> str:
    """用户导向维度 → 提示词硬约束块(两段共用:auto 的维度不出行,零影响)。"""
    rules = [
        dialogue_style_rule(getattr(clip, "dialogue_style", "") or ""),
        pacing_rule(getattr(clip, "pacing", "") or ""),
        intensity_rule(getattr(clip, "intensity", "") or ""),
    ]
    hints = (getattr(clip, "style_hints", "") or "").strip()
    if hints:
        rules.append(
            f"**氛围关键词(必须自然融入画风卡与各格提示词的氛围)**:{hints}"
        )
    rules = [r for r in rules if r]
    if not rules:
        return ""
    return "【导向(用户指定,硬约束)】" + ";".join(rules) + "\n"


def theme_display(clip: MoodClip) -> str:
    """列表用短标签。"""
    if _clip_mode(clip) == "free":
        return clip.custom_theme.strip() or "自由创作"
    if _clip_mode(clip) == "play":
        if clip.theme in _PLAY_MAP:
            return _PLAY_MAP[clip.theme]["label"]
        return clip.custom_theme.strip() or "自定义玩法"
    if clip.theme in _THEME_MAP:
        return _THEME_MAP[clip.theme]["label"]
    return clip.custom_theme.strip() or "自定义"


def shot_hint(duration_s: int) -> str:
    """时长 → 建议镜头数(提示词用)。"""
    return "3-5 格,每格 2-6 秒" if duration_s <= 15 else "5-7 格,每格 2-6 秒"


# =============== 切段(复用 media.segments 单点;时间轴与 SRT 同口径) ===============

def group_chunks(shots: list[dict], chunk_s: int) -> list[dict]:
    """把分镜 dict 列表按镜头边界贪心聚段,返回带起止时间码的段列表。"""
    return plan_chunks(shots, chunk_s)


# =============== 序列化 ===============

STATUS_CN = {
    "draft": "待生成",
    "generated": "候选已出",
    "picked": "已选定",
}


def clip_dict(row: MoodClip, with_candidates: bool = True) -> dict:
    d = {
        "id": row.id,
        "source_project_id": row.source_project_id,
        "mode": _clip_mode(row),
        "theme": row.theme,
        "custom_theme": row.custom_theme,
        "theme_display": theme_display(row),
        "duration_s": row.duration_s,
        "direction": row.direction or "live",
        "direction_label": direction_label(row.direction or "live"),
        "inspiration": row.inspiration,
        "dialogue_style": getattr(row, "dialogue_style", "") or "auto",
        "pacing": getattr(row, "pacing", "") or "auto",
        "intensity": getattr(row, "intensity", "") or "auto",
        "style_hints": getattr(row, "style_hints", "") or "",
        "style_name": row.style_name,
        "style_cn": row.style_cn,
        "style_en": row.style_en,
        "negative": row.negative,
        "chosen": row.chosen,
        "clip": row.clip or {},
        "status": row.status,
        "status_cn": STATUS_CN.get(row.status, row.status),
    }
    if with_candidates:
        d["candidates"] = row.candidates or []
    return d
