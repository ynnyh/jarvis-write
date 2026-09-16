# app/engines/style_profile.py
# -*- coding: utf-8 -*-
"""结构化文风画像(docs/20 同批「文风可视化+进化」)。

一份真相:projects.style_profile(JSON)是唯一存储——画像卡的投影、续集分析
的产物、作者的手改,全部写回这一列;生成时由 render_style_profile_block 渲染
进 style_block(与 style_memo 同路,草稿/定稿全生效)。

六维是作者点名要抽离的「写作手法」的落位:视角/句式节奏/对话密度/修辞/基调/钩法。
每维一条**可执行的写作指令**(不是形容词夸奖),作者在画像卡上改,保存即生效。
"""
from __future__ import annotations

from datetime import datetime, timezone

# 维度定义(顺序即展示顺序):key / 中文名 / 编辑提示
PROFILE_DIMS: list[tuple[str, str, str]] = [
    ("perspective", "叙事视角", "第几人称、跟谁的视角、视角是否切换"),
    ("rhythm", "句式节奏", "句长偏好、长短句怎么交替、段落节奏"),
    ("dialogue", "对话密度", "对话占多少、对白风格(书面/口语/方言腔)"),
    ("rhetoric", "修辞惯用", "高频修辞与意象、惯用手法(比喻/白描/留白…)"),
    ("mood", "氛围基调", "整体调性、场面氛围怎么烘、情绪浓度"),
    ("hook", "起势与钩法", "章头怎么起势、章尾怎么留钩"),
]

_DIM_KEYS = [k for k, _, _ in PROFILE_DIMS]
_DIM_LABELS = dict((k, label) for k, label, _ in PROFILE_DIMS)
_DIM_HINTS = dict((k, hint) for k, _, hint in PROFILE_DIMS)

# 画像历史保留上限(与章节订单同一轻量哲学)
_HISTORY_KEEP = 10


def empty_dims() -> dict[str, dict]:
    """空白六维(全空 = 画像不存在,注入零字节,老书零影响)。"""
    now = datetime.now(timezone.utc).isoformat()
    return {
        k: {"text": "", "source": "", "at": now}
        for k in _DIM_KEYS
    }


def normalize_profile(raw: dict | None) -> dict:
    """规整成标准形状:缺维补空、脏形状就地清洗;不抛错(画像坏了不能挡生成)。"""
    if not isinstance(raw, dict):
        return {"dims": empty_dims(), "version": 0, "history": []}
    raw_dims = raw.get("dims") if isinstance(raw.get("dims"), dict) else {}
    dims = empty_dims()
    for k in _DIM_KEYS:
        d = raw_dims.get(k)
        if isinstance(d, dict):
            dims[k] = {
                "text": str(d.get("text") or "").strip(),
                "source": str(d.get("source") or "")[:40],
                "at": str(d.get("at") or "")[:40],
            }
        elif isinstance(d, str):  # 容错:直接给了字符串
            dims[k] = {"text": d.strip(), "source": "", "at": ""}
    return {
        "dims": dims,
        "version": int(raw.get("version") or 0),
        "history": list(raw.get("history") or [])[-_HISTORY_KEEP:],
    }


def profile_is_empty(profile: dict | None) -> bool:
    p = normalize_profile(profile)
    return not any(d["text"] for d in p["dims"].values())


def render_style_profile_block(profile: dict | None) -> str:
    """画像 → style_block 追加块。空画像返回空串,prompt 字节级不变(存量书零影响)。"""
    p = normalize_profile(profile)
    lines = [
        f"- {_DIM_LABELS[k]}:{p['dims'][k]['text']}"
        for k in _DIM_KEYS
        if p["dims"][k]["text"]
    ]
    if not lines:
        return ""
    return (
        "【文风画像(作者确认的本书笔法,每次行文都要照此执行)】\n"
        + "\n".join(lines)
        + "\n"
    )


def merge_dims(base: dict | None, overrides: dict | None, source: str = "手改") -> dict:
    """作者在画像卡上保存:覆盖有字的维度并标注来源;空白维度不动(不清作者内容)。"""
    b = normalize_profile(base)
    now = datetime.now(timezone.utc).isoformat()
    for k in _DIM_KEYS:
        incoming = (overrides or {}).get(k)
        if isinstance(incoming, str) and incoming.strip():
            b["dims"][k] = {"text": incoming.strip(), "source": source, "at": now}
        elif isinstance(incoming, dict) and str(incoming.get("text") or "").strip():
            b["dims"][k] = {
                "text": str(incoming["text"]).strip(),
                "source": str(incoming.get("source") or source)[:40],
                "at": str(incoming.get("at") or now)[:40],
            }
    return b


def archive_history(profile: dict | None) -> list[dict]:
    """保存前把当前版本推入历史(轻量 JSON,沿订单表哲学)。"""
    p = normalize_profile(profile)
    if not any(d["text"] for d in p["dims"].values()):
        return p["history"]  # 空画像不进历史
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "version": p["version"],
        "dims": p["dims"],
        "at": now,
    }
    return (p["history"] + [entry])[-_HISTORY_KEEP:]


def profile_from_analysis(analysis: dict) -> dict:
    """前作分析/章节提取的产物 → 标准画像(来源标注为分析通道)。"""
    dims = empty_dims()
    incoming = analysis.get("style_profile") if isinstance(analysis, dict) else None
    raw_dims = incoming if isinstance(incoming, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    for k in _DIM_KEYS:
        text = str(raw_dims.get(k) or "").strip()
        if text:
            dims[k] = {"text": text, "source": "前作分析", "at": now}
    return {"dims": dims, "version": 0, "history": []}
