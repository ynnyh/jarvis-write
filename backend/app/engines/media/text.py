# app/engines/media/text.py
# -*- coding: utf-8 -*-
"""三条出片线共用的脏值收敛小件:LLM 字段裁剪、整数收敛、带 BOM 的 CSV 文本。

都长在 `drama/common.py` 里,而宣传片与情绪短片处处在用——依赖方向反了。定义挪到这里,
`drama/common.py` 改为从这里导入并对内再导出(漫剧内部八十多处调用不动)。
"""
from __future__ import annotations

import csv
import io


def clip(s: object, width: int) -> str:
    """LLM 字段裁剪:转字符串、去首尾空白、限长。"""
    return str(s or "").strip()[:width]


def coerce_int(raw: object, default: int, lo: int = 0, hi: int = 10**6) -> int:
    """把 LLM 的 "4"/4.0/脏值收敛成 [lo, hi] 内的 int。"""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def csv_text(header: list[str], rows: list[list]) -> str:
    """表格 → CSV 文本,**带 UTF-8 BOM**:Excel 打开中文不乱码(少了它必乱)。

    三条线的分镜表列数差得远(漫剧是 23 列的逐格施工单,宣传片 10 列),
    共用的只有这层 writer + BOM 的样板——但 BOM 这件事漏一次就是一张乱码表。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return "\ufeff" + buf.getvalue()
