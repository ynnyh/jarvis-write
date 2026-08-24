# app/engines/media/segments.py
# -*- coding: utf-8 -*-
"""切段单点:把分镜按「一次生成一段」的上限并成段,并给出与 SRT 同轴的时间码。

为什么要单点化:漫剧、宣传片、情绪短片三条线各写过一份贪心聚段,改一处漏两处
(时间码口径、超限标注、字幕对位都得一模一样,否则导出的 SRT 和视频段对不上)。
这里只放**确定性**逻辑:边界怎么切、时间码怎么累、超限怎么标。LLM 写的内容
(运动提示词、拼接指引)由各引擎自己贴到 `chunk_rows` 的结果上。

两条口径必须全站一致:
1. **绝不在镜头中间断开**——段边界永远落在镜头边界上;
2. 单个镜头本身就超上限时**独立成段并标 `over_limit`**,不硬塞、也不静默截断
   (生成侧靠这个标记决定「同一首帧连生两段再接」)。

分镜项既可能是 dict(情绪短片:LLM 产出直接存 JSON),也可能是 ORM 行
(宣传片/漫剧:PromoShot / DramaShot),所以字段一律走 `field()` 读,
不要求调用方先转换格式。
"""
from __future__ import annotations

from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")


def field(item: object, name: str, default: object = None) -> object:
    """读分镜字段:dict 走 key,ORM 行走属性。取不到给 default。"""
    if isinstance(item, dict):
        v = item.get(name, default)
    else:
        v = getattr(item, name, default)
    return default if v is None else v


def dur_of(item: object) -> int:
    """镜头时长(秒),脏值算 0——0 秒的格不影响分段边界,但会被算进段内。"""
    try:
        return int(field(item, "duration_s", 0) or 0)
    except (TypeError, ValueError):
        return 0


def seq_of(item: object) -> int:
    try:
        return int(field(item, "seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


def text_of(item: object, name: str) -> str:
    return str(field(item, name, "") or "").strip()


def group_by_limit(
    shots: Iterable[T],
    limit_s: int,
    can_join: Callable[[Sequence[T], T, int], bool] | None = None,
) -> list[list[T]]:
    """镜头边界贪心聚段:装得下就装,装不下开新段。

    `limit_s` 是唯一的默认约束(段内总时长不超上限)。需要更严的内聚条件
    (漫剧:同场景 / 不引入新角色 / 一段最多一条台词)时传 `can_join`:
    入参是(当前段, 待并入的镜头, 当前段已累计秒数),返回能不能并。
    注意 `can_join` 只做「加严」——它返回 True 时仍要自己把时长判进去,
    因为并段的时长口径本身就是它要覆盖的条件之一。
    """
    groups: list[list[T]] = []
    cur: list[T] = []
    cur_s = 0
    for s in shots:
        d = dur_of(s)
        if cur:
            # 空段永远收下第一格(哪怕它自己就超上限——那种格独立成段并标 over_limit),
            # 所以 can_join 只在段内已有镜头时才问,回调不必自己防 cur[0] 越界。
            joinable = (
                can_join(cur, s, cur_s) if can_join is not None else (cur_s + d <= limit_s)
            )
            if not joinable:
                groups.append(cur)
                cur, cur_s = [], 0
        cur.append(s)
        cur_s += d
    if cur:
        groups.append(cur)
    return groups


def chunk_rows(groups: Sequence[Sequence[object]], limit_s: int) -> list[dict]:
    """段列表 → 带时间码的行。时间码是从 0 累计的整秒轴,与导出的 SRT 同一根轴。

    字幕用换行拼(一段里多句台词各占一行),空台词不占行——否则 SRT 里会出现空行。
    """
    rows: list[dict] = []
    t = 0
    for g in groups:
        dur = sum(dur_of(s) for s in g)
        rows.append(
            {
                "index": len(rows) + 1,
                "start_s": t,
                "end_s": t + dur,
                "duration_s": dur,
                "over_limit": dur > limit_s,
                "shot_seqs": [seq_of(s) for s in g],
                "scenes": [text_of(s, "scene_name") for s in g if text_of(s, "scene_name")],
                "subtitle": "\n".join(
                    text_of(s, "dialogue") for s in g if text_of(s, "dialogue")
                ),
            }
        )
        t += dur
    return rows


def plan_chunks(shots: Iterable[object], limit_s: int) -> list[dict]:
    """最常用的一步到位:并段 + 时间码。宣传片/情绪短片直接用这个。"""
    return chunk_rows(group_by_limit(shots, limit_s), limit_s)
