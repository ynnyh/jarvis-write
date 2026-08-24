# app/engines/media/subtitles.py
# -*- coding: utf-8 -*-
"""三条出片线共用的字幕内核:秒 → SRT 时间码、(时长, 文本) 序列 → 标准 SRT。

原先这份内核长在 `drama/exporter.py` 里,宣传片与情绪短片各自跨模块去拿它那个私有的
`_srt_blocks` —— 既拿了私有名,又把「三条线共用的确定性件」压在其中一条线底下
(依赖方向反了:宣传片/短片不是漫剧的下游)。现在挪到 media 并转公开,口径只此一份:

- 时间轴按分镜时长**累计**,与剪辑清单、切段(media.segments)同一根轴;
- **有文本才有字幕条**,空台词的格时长仍然计入时间轴(不然后面的字幕整体前移)。
"""
from __future__ import annotations


def srt_ts(sec: float) -> str:
    """秒 → SRT 时间码 HH:MM:SS,mmm。"""
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_blocks(items: list[tuple[int, str]]) -> str:
    """(时长秒, 字幕文本) 序列 → 标准 SRT。有文本才有字幕条;空条时长仍计入时间轴。"""
    blocks: list[str] = []
    t = 0.0
    idx = 0
    for duration, text in items:
        start, end = t, t + duration
        t = end
        text = (text or "").strip()
        if not text:
            continue
        idx += 1
        blocks.append(f"{idx}\n{srt_ts(start)} --> {srt_ts(end)}\n{text}\n")
    return "\n".join(blocks)


def srt_from_rows(shots: list[dict]) -> str:
    """dict 版分镜(预告片/情绪短片的 JSON 分镜)→ SRT,与 ORM 版同一时间轴口径。

    这段脏值收敛(duration_s 可能是 "3"/None、dialogue 可能缺键)漫剧预告片与
    情绪短片此前各写了一遍一模一样的 lambda,合到这里。
    """
    return srt_blocks(
        [(int(s.get("duration_s") or 0), str(s.get("dialogue") or "")) for s in shots]
    )
