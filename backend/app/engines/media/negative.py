# app/engines/media/negative.py
# -*- coding: utf-8 -*-
"""负面词基座单点:共享禁用项 + 本格规避项的合流。

为什么要单点化:负面词是「图生视频」最容易漏的一轨——漏了就直接出片出现
多指、脸部变形、水印,而这些 bug 只在用户拿成片时暴露,测试里看不见。
`anime/episodes.py` 早就把它写成了常量 `_SHOTCARD_NEGATIVE_BASE`,
但那条路径只服务动漫线镜头卡;drama / promo / clips 三条线各自散着写、
各写各的(`promo/prompt_render.py:82` 另有一份自己的基座)。

统一到本文件的三条口径:
1. **共享基座**:任何一格都该禁的东西(水印、文字、画风漂移、多指……),
   与线无关,写在 BASE 里;
2. **本格规避**:这一格额外要避的(人名出现在画面里、分镜线穿帮、场景跳变……),
   由调用方按本格内容给出;
3. **合流时保序去重**:LLM 已经吐过某一项时不要重复——重复的负面词会让模型
   过度加权,反而把主体也一起压掉。

它是 `media/` 的叶子件:只做纯字符串处理,不认识任何一条线的业务
(被 `test_media_does_not_depend_on_any_line` 门禁守住)。
"""
from __future__ import annotations

# 共享基座:与线无关、任何一格都该禁的。按「先主体后环境」排列——
# 视频模型对靠前的否定更敏感,「脸部变形/多指」这类伤主体的排前面。
BASE: tuple[str, ...] = (
    "脸部变形", "多指", "肢体扭曲",
    "文字", "水印", "字幕",
    "画风漂移", "场景切换",
)

# 各线可选追加项:不是禁用,是「这一类片子特别容易出的问题」。
# 只放确定性的通用项,业务特有的规避由调用方按本格内容给。
LINE_EXTRA: dict[str, tuple[str, ...]] = {
    "drama": ("格与格之间人物衣着突变", "跨格出现镜头外的多余人物"),
    "promo": ("画面出现字幕条与台标", "口播人物与旁白画面对不上"),
    "clips": ("情绪转折处画面突变", "同一角色跨镜换脸"),
    "anime": ("定格帧出现说话口型抖动",),
}


def merge(*extra: object) -> list[str]:
    """共享基座 + 各组追加项 → 保序去重的负面词列表。

    参数允许传 str / list / tuple / None(调用方手上多半是 LLM 返回的
    脏值),非序列一律跳过而不是抛错——负面词缺失不该让整格生成失败。
    """
    out: list[str] = []
    seen: set[str] = set()
    for group in (BASE, *extra):
        items = [group] if isinstance(group, str) else group
        if not isinstance(items, (list, tuple)):
            continue
        for raw in items:
            word = str(raw or "").strip()
            if word and word not in seen:
                seen.add(word)
                out.append(word)
    return out


def for_line(line: str, *extra: object) -> list[str]:
    """某条线的共享基座 + 该线追加项 + 调用方本格规避。"""
    base_extra = LINE_EXTRA.get(line, ())
    return merge(base_extra, *extra)


def as_text(line: str = "", *extra: object) -> str:
    """渲染成提示词里的一行「负面:」内容。空结果返回空串。"""
    words = for_line(line, *extra)
    return "、".join(words) if words else ""


def ensure_base(negative: str, line: str) -> str:
    """负面词兜底:风格卡与 LLM 都没给负面词时,补上该线的共享基座。

    为什么是「兜底」而不是「强制前置」:风格卡里填的负面词是作者自己挑的,
    比基座更贴合这片子,不该被基座覆盖或加权(重复的否定会让模型过度压制主体)。
    与 `anchors.ensure_anchor` 同一个思路——缺了才补,有了就不动。

    这是 `media/negative.py` 唯一的对外入口语义:调用方照旧先跑
    `anchors.merge_negative(本格, 风格卡)`,拿到的结果再过一道本函数。
    """
    return (negative or "").strip() or as_text(line)
