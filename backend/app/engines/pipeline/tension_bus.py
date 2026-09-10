# app/engines/pipeline/tension_bus.py
# -*- coding: utf-8 -*-
"""全书张力总线:一条贯穿全书的节奏曲线,每章从它上面切一段。

为什么需要「总线」而不是每章各自定曲线(docs/15 §3.2):

章内张力曲线解决的是「这一章内部有没有起伏」;但如果每章的曲线都自成一格、
彼此无关,全书读下来还是一条平线——读者感受到的是「每章都挺工整,但整体没劲」。
真正的留存靠的是**跨章的节奏**:铺垫几章 → 小高潮 → 再压 → 大爆发 → 余韵。
这是「卷」级别的编排,章级拿不到这个信息,因为它不知道自己在全书里的位置。

设计上刻意做成一维单曲线(D6:先单维,跑通再拆维):
  张力值 1-5 一条线,卷纲(macro_plan)提供骨架,每卷的张力按卷内位置升降,
  卷与卷之间逐卷抬高(网文的「格局/赌注逐卷抬升」在节奏上的等价物),
  最后一卷冲到全局峰值后收束。

总线是「建议」不是「命令」:
  它产出每章的目标张力(target),章内分场时作为基准;场景卡的张力曲线仍可
  在基准上做局部起伏(实测手感优先)。总线只保证一件事——**跨章不许是一条平线**。

零 LLM:
  纯确定性推导,不额外花钱。卷纲本来就是 LLM 生成的,总线是对它的二次编排。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis-write.tension_bus")

# 张力值域,与场景卡同一口径(1 压住 → 5 爆发)
TENSION_MIN, TENSION_MAX = 1, 5

# 章节定位 → 张力基准修正。定位是蓝图已有的字段(常规推进/关键转折/高潮/过渡/收束),
# 拿它做修正比再问一次模型便宜且稳定。
_ROLE_BONUS: dict[str, int] = {
    "高潮": 2,
    "决战": 2,
    "爆发": 2,
    "关键转折": 1,
    "转折": 1,
    "收束": 1,
    "过渡": -1,
    "铺垫": -1,
    "缓冲": -1,
    "日常": -1,
}

# 悬念密度 → 张力修正(蓝图已有字段)
_SUSPENSE_BONUS: dict[str, int] = {"高": 1, "中": 0, "低": -1}

# 卷内的张力波形:开始 → 中段 → 结束。卷末抬到卷内最高,但**不是全局最高**——
# 全局最高留给全书尾声段(最后一卷的峰值),这样才有"越读越上头"。
_VOLUME_WAVE = (0, 1, 1, 2, 1, 2)


def _clamp(v: int) -> int:
    return max(TENSION_MIN, min(TENSION_MAX, int(v)))


def _role_bonus(chapter_role: str) -> int:
    role = (chapter_role or "").strip()
    if not role:
        return 0
    # 定位字段常是「高潮」/「关键转折」这类词,也可能是组合描述,做包含匹配
    for key, bonus in _ROLE_BONUS.items():
        if key in role:
            return bonus
    return 0


def _suspense_bonus(suspense_level: str) -> int:
    return _SUSPENSE_BONUS.get((suspense_level or "").strip(), 0)


def _segment_of(segments: list[dict], chapter: int) -> tuple[dict | None, int, int]:
    """chapter 落在哪一卷,以及它在该卷内的位置(第几章 / 共几章)。"""
    for seg in segments or []:
        start, end = int(seg.get("start") or 1), int(seg.get("end") or 0)
        if start <= chapter <= end:
            total = max(1, end - start + 1)
            return seg, chapter - start, total
    return None, 0, 1


def _volume_base(index: int, total_volumes: int) -> int:
    """第 index 卷(0 基)的基础张力:逐卷抬高,末卷到 4(全局峰值留给章级叠加)。"""
    if total_volumes <= 1:
        return 3
    # 2 卷 → 3/4;3 卷 → 3/3/4;5 卷 → 2/3/3/4/4 …
    span = 4 - 2  # 从 2 抬到 4
    step = span / max(1, total_volumes - 1)
    return _clamp(round(2 + step * index))


def chapter_tension_target(
    chapter_number: int,
    *,
    chapter_role: str = "",
    suspense_level: str = "",
    macro_plan: list[dict] | None = None,
) -> int:
    """算出某一章的目标张力(1-5)。

    三步:卷内波形(位置) → 卷级抬升(越往后越高) → 章定位/悬念修正。
    这是「建议值」:场景切分时作为基准,允许在章内做局部起伏。
    """
    segments = macro_plan or []
    seg, offset, seg_len = _segment_of(segments, chapter_number)
    if seg is None:
        # 没有卷纲(短篇/早期项目):退化成按章号在全书中段位取波,仍保证不平
        wave = _VOLUME_WAVE[(max(1, chapter_number) - 1) % len(_VOLUME_WAVE)]
        base = _clamp(3 + (1 if wave >= 2 else -1 if wave == 0 else 0))
    else:
        vol_index = next(
            (i for i, s in enumerate(segments) if s is seg), 0
        )
        base = _volume_base(vol_index, len(segments))
        # 卷内波形:把 _VOLUME_WAVE 按卷内相对位置采样
        if seg_len <= 1:
            wave = 1
        else:
            pos = int(offset / max(1, seg_len - 1) * (len(_VOLUME_WAVE) - 1))
            wave = _VOLUME_WAVE[max(0, min(len(_VOLUME_WAVE) - 1, pos))]
        base = _clamp(base + (wave - 1))
        # 卷末抬一档:卷收尾要留钩子,不能平着过去(但已在 4/5 的不再加)
        if offset == seg_len - 1 and base < 4:
            base = _clamp(base + 1)

    target = base + _role_bonus(chapter_role) + _suspense_bonus(suspense_level)
    return _clamp(target)


def bus_for_book(
    *,
    target_chapters: int,
    macro_plan: list[dict] | None = None,
    outlines: list[Any] | None = None,
) -> list[int]:
    """算出全书每章的目标张力序列(1 基下标即章号)。

    outlines 提供章定位/悬念密度做修正;缺失时只用卷纲与位置。
    """
    by_num: dict[int, Any] = {}
    for o in outlines or []:
        n = getattr(o, "chapter_number", None)
        if n:
            by_num[int(n)] = o
    total = int(target_chapters or 0)
    if total <= 0:
        # 目标章数未定(项目刚建/脏数据):返回空曲线,而不是凭空造出第 1 章
        return []
    return [
        chapter_tension_target(
            n,
            chapter_role=getattr(by_num.get(n), "chapter_role", "") or "",
            suspense_level=getattr(by_num.get(n), "suspense_level", "") or "",
            macro_plan=macro_plan,
        )
        for n in range(1, total + 1)
    ]


def is_flat(levels: list[int], *, min_span: int = 2) -> bool:
    """判断一条曲线是不是「平」的(全书节奏的最主要病症)。

    平的定义:极差小于 min_span。`[3,3,3,3]` 是平,`[3,4,3,4]` 也当平(只差 1,
    读者感觉不到),`[2,3,4,5,3]` 才算有起伏。默认 min_span=2 与场景级的
    `_has_curve` 同一口径:相邻差 2 档以上才叫「有落差」。
    """
    if len(levels) < 2:
        return True
    return (max(levels) - min(levels)) < min_span


def flatness_report(levels: list[int]) -> dict[str, Any]:
    """给出一份可读的节奏体检:极差 / 均值 / 是否平 / 最长同值段。

    前端「本书健康报告」与评测轨用它出数;不解释、只报事实。
    """
    if not levels:
        return {"span": 0, "mean": 0, "flat": True, "longest_run": 0, "peak_at": 0}
    peak_idx = levels.index(max(levels)) + 1
    longest, cur = 1, 1
    for i in range(1, len(levels)):
        cur = cur + 1 if levels[i] == levels[i - 1] else 1
        longest = max(longest, cur)
    return {
        "span": max(levels) - min(levels),
        "mean": round(sum(levels) / len(levels), 2),
        "flat": is_flat(levels),
        "longest_run": longest,
        "peak_at": peak_idx,
        "chapters": len(levels),
    }


# 章级提示词注入块:把「你这一章在全书里的位置」告诉模型。
# 这是总线真正落地的地方——光有数据模型没用,得进到 prompt 里。
_TENSION_BUS_BLOCK = """\
【全书节奏位置(这一章在整本书里的坐标,写的时候心里要有这个数)】
第{chapter}章 / 共{total}章,目标张力 {target}/5({label})。
{neighbors}
{directive}
上面这句不是建议,是编排:该压的章不要抢戏,该爆的章不要留手。
章内分场时以 {target} 为基准做起伏——平均用力是这章最不该出现的问题。"""

_TENSION_LABELS: dict[int, str] = {
    1: "全程蓄力,一个爆发点都不给",
    2: "低烧,让不安慢慢渗出来",
    3: "常规推进,保持牵引力",
    4: "有明确情绪高点,要砸下去",
    5: "全书级别的爆发段,全力兑现",
}

_TENSION_BUS_DIRECTIVES: dict[int, str] = {
    1: "具体做法:把冲突往后推,这一章用细节和不安撑住;该说的信息说清,该埋的埋深,"
       "但不要提前把力气花掉。",
    2: "具体做法:写「事情正在变坏」的过程而不是结果;让读者替人物着急,但还没到爆的时候。",
    3: "具体做法:推进关系与信息,给一个小满足点(一个揭晓、一次小胜),让读者愿意继续读。",
    4: "具体做法:这一章必须有一个能被记住的场面——正面冲突、揭晓、失控都行,"
       "要写足、写透,别点到为止。",
    5: "具体做法:前面攒的力在这一章一次性还清。最强的画面、最狠的一击、最烫的情绪"
       "全放这里,不留手、不解释、不总结。",
}


def tension_bus_block(
    chapter_number: int,
    *,
    target_chapters: int,
    macro_plan: list[dict] | None = None,
    chapter_role: str = "",
    suspense_level: str = "",
) -> str:
    """生成注入正文 prompt 的「全书节奏位置」块。空章节信息时返回空串。"""
    if not target_chapters or chapter_number <= 0:
        return ""
    target = chapter_tension_target(
        chapter_number,
        chapter_role=chapter_role,
        suspense_level=suspense_level,
        macro_plan=macro_plan,
    )
    prev_t = (
        chapter_tension_target(
            chapter_number - 1, macro_plan=macro_plan
        )
        if chapter_number > 1
        else None
    )
    next_t = (
        chapter_tension_target(
            chapter_number + 1, macro_plan=macro_plan
        )
        if chapter_number < target_chapters
        else None
    )
    bits = []
    if prev_t is not None:
        bits.append(f"上一章张力 {prev_t}")
    if next_t is not None:
        bits.append(f"下一章张力 {next_t}")
    neighbors = ("(邻近章节:" + "、".join(bits) + ")") if bits else ""
    return _TENSION_BUS_BLOCK.format(
        chapter=chapter_number,
        total=target_chapters,
        target=target,
        label=_TENSION_LABELS.get(target, ""),
        neighbors=neighbors,
        directive=_TENSION_BUS_DIRECTIVES.get(target, ""),
    )


def adjust_scene_waves(
    scene_targets: list[int],
    *,
    chapter_target: int,
) -> list[int]:
    """把章级目标张力摊到各场,形成一条以 chapter_target 为峰值的场间曲线。

    scene_targets 是模型给的原始场级张力(可能平)。做法:
      · 若模型曲线本身有起伏(极差 >= 2),原样保留(尊重模型的判断);
      · 否则按「低 → 峰值 → 回落」重塑,峰值位置取倒数第二场(不在最后一场——
        最后一场是余波,高潮摆最后会显得为了炸而炸);
      · 峰值至少 4:章目标是 3 的章也该有一个拎得起来的点,否则整章平着走。

    这是「章内曲线」与「全书总线」的接口:总线给章一个 target,这里把它展开成场。
    """
    n = len(scene_targets)
    if n == 0:
        return []
    if n == 1:
        return [_clamp(chapter_target)]
    if max(scene_targets) - min(scene_targets) >= 2:
        return [_clamp(t) for t in scene_targets]

    # 峰值位置:倒数第二场;2 场时取第一场
    peak_idx = n - 2 if n > 2 else 0
    shaped: list[int] = []
    for i in range(n):
        if i == peak_idx:
            shaped.append(_clamp(max(chapter_target, 4)))
        elif i == n - 1:
            shaped.append(_clamp(chapter_target - 2))
        elif i < peak_idx:
            # 峰前逐步抬升:从目标-1 抬到目标
            steps = peak_idx if peak_idx > 0 else 1
            shaped.append(_clamp(chapter_target - 1 + (i * 1.0 / steps)))
        else:
            # 峰值之后、最后一场之前:回落到目标-1
            shaped.append(_clamp(chapter_target - 1))
    return shaped
