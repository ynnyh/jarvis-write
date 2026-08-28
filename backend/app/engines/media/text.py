# app/engines/media/text.py
# -*- coding: utf-8 -*-
"""三条出片线共用的脏值收敛小件:LLM 字段裁剪、整数收敛、带 BOM 的 CSV 文本。

都长在 `drama/common.py` 里,而宣传片与情绪短片处处在用——依赖方向反了。定义挪到这里,
`drama/common.py` 改为从这里导入并对内再导出(漫剧内部八十多处调用不动)。
"""
from __future__ import annotations

import csv
import io
import re


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


def split_character_desc(desc: str, characters: list[str]) -> dict[str, str]:
    """把一格里混写的多角色外貌按「角色名」切给各角色,返回 {角色名: 描段}。

    模型契约是多角色用「角色名:…」分段(见各线展开提示词),但实测两种走样都出过:
    - 第一个角色的冒号被吞:「小朋友女/5岁…。妈妈:女/30岁…」;
    - 正常分段里夹了对别的角色的提(mid-sentence 提到"妈妈的包")。
    切法:按 characters 顺序找每个角色的名字锚——优先「名字+冒号」,退而求其次
    找裸名字;相邻锚之间的文字归前一个锚的角色;第一个锚之前的引子,只在它以
    某个角色名开头时归那个角色(正是冒号被吞的形态),否则丢弃;单角色镜头整段
    直接归它。找不到的名字不发明内容(缺就缺着)。
    """
    text = (desc or "").strip()
    names = [str(n).strip() for n in (characters or []) if str(n or "").strip() and n != "旁白"]
    if not text:
        return {n: "" for n in names}
    if len(names) == 1:
        return {names[0]: text}

    # 找每个角色的锚:先找「名字+冒号」,没有再退化找裸名字;各自从上一个锚之后找,
    # 避免后文对前文角色的再次提及把锚拉回去
    anchors: list[tuple[int, int, str]] = []  # (名字起始位, 名字长度, 名字)
    search_from = 0
    for name in names:
        hit = re.search(re.escape(name) + r"\s*[:：]", text[search_from:])
        if hit is None:
            hit = re.search(re.escape(name), text[search_from:])
        if hit is None:
            continue
        start = search_from + hit.start()
        anchors.append((start, len(name), name))
        search_from = start + len(name)

    if not anchors:
        return {n: "" for n in names}

    def _clean(chunk: str) -> str:
        return chunk.strip(" \t\r\n。.、;;::： ")

    out: dict[str, str] = {}
    # 引子:第一个锚之前的文字,只有以某个角色名开头才归那个角色(冒号被吞的形态)
    first_pos = anchors[0][0]
    lead = _clean(text[:first_pos])
    if lead and text.lstrip().startswith(anchors[0][2]):
        out[anchors[0][2]] = lead
    for i, (start, ln, name) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        span = _clean(text[start:end])
        if span:
            prev = out.get(name)
            out[name] = f"{prev};{span}" if prev else span
    return out


def speaker_of(dialogue: str, lines: list) -> str:
    """台词原文 → 说话人(按文本精确对齐剧本/本子 lines;对不上就空着不猜)。

    整片提示词的镜头表要写「谁说了这句」,但各线分镜里台词都不带说话人,
    只能回查剧本 lines 做文本精确匹配——匹配不上宁可空着,猜错更糟。
    """
    key = str(dialogue or "").strip()
    if not key:
        return ""
    for line in lines or []:
        if isinstance(line, dict) and str(line.get("text") or "").strip() == key:
            return str(line.get("speaker") or "").strip()
    return ""


def strip_fences(text: str) -> str:
    """剥掉模型偶尔裹上来的 markdown 围栏——上屏的就是纯文本,不带这些赘余。"""
    t = str(text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
