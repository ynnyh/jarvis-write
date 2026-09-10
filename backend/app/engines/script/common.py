# app/engines/script/common.py
# -*- coding: utf-8 -*-
"""剧本工坊引擎共用件:格式校验、集末交接契约、版本快照、序列化。

2026-09-10 建:此前剧本生成贴在路由里,一次模型调用、返回文本直接落库——
没有格式校验(模型输出散文/JSON 照样存)、没有重试、没有集间衔接状态
(只靠上一集末 400 字让模型自己悟)。这里补上剧本线自己的最小质量件,
思路沿用小说侧已验证过的三件套:格式门禁 / 交接契约 / 版本快照。

契约与快照存 ScriptEpisode.extra(JSON 列),不新增表——剧本集数量级很小,
JSON 列足够,且免去迁移。契约提取失败只降级(标 failed),不阻塞正本入库。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# =============== 篇幅与截取 ===============
MIN_CONTENT_CHARS = 120      # 低于此长度视为输出被截断/空壳,不算写成
PREV_TAIL_CHARS = 400        # 上一集结尾原文注入长度(衔接语感)
END_STATE_TAIL_CHARS = 1200  # 提取集末契约时看的结尾长度
MAX_CONTENT_CHARS = 60000    # 防跑飞

# =============== 状态目录 ===============
STATUS_CN = {
    "empty": "待生成", "outlined": "已大纲",
    "drafted": "已出稿", "approved": "已通过",
}

# 场景标题行:可选 markdown # / 「第N场」,后接内外景标记。
# 这是 Fountain 风格的骨架,没有它说明模型没按剧本格式写(多半输出了散文或 JSON)。
_SCENE_HEAD_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:第\s*[0-9零一二三四五六七八九十百]+\s*[场幕集]\s*[、.·:：-]?\s*)?"
    r"(?:内景|外景|内外景|室内|室外|INT\.?|EXT\.?|INT/EXT\.?)",
    re.MULTILINE | re.IGNORECASE,
)


def strip_meta(text: str) -> str:
    """清理模型输出的元信息:markdown 围栏与「第N集」标题行。

    与小说侧 _strip_meta 同思路,但剧本允许保留场景标题行,只剥外层的
    ``` 围栏和「第 2 集」这类整集标题。
    """
    s = (text or "").strip()
    m = re.search(r"```(?:[a-zA-Z]*)?\s*(.*?)```", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    lines = s.splitlines()
    while lines:
        head = lines[0].strip().lstrip("#").strip()
        if re.match(r"^第\s*[0-9零一二三四五六七八九十百]+\s*集\b", head) and len(head) < 40:
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def validate_script_content(text: str) -> tuple[bool, str]:
    """剧本正文格式门禁。返回 (是否可用, 失败原因)。

    只挡「明显没写成」三类:空壳/被截断、输出成了 JSON 或代码块、没有场景
    标题行。不评判文笔——那不是确定性代码该管的事。
    """
    s = (text or "").strip()
    if not s:
        return False, "模型返回空内容"
    if len(s) < MIN_CONTENT_CHARS:
        return False, f"正文只有 {len(s)} 字,疑似输出被截断(低于 {MIN_CONTENT_CHARS} 字)"
    if s.startswith("{") or s.startswith("[") or s.startswith("```"):
        return False, "输出的是 JSON / 代码块,不是剧本正文"
    if not _SCENE_HEAD_RE.search(s):
        return False, "没有一个场景标题行(内景/外景·日/夜·地点),格式崩坏"
    return True, ""


# =============== 集末交接契约(存 episode.extra)===============

def end_state_of(episode: Any) -> dict | None:
    """读取集末契约;提取失败或未提取 → None(回退现状,不报错)。"""
    extra = episode.extra if isinstance(episode.extra, dict) else {}
    if extra.get("end_state_status") != "ok":
        return None
    state = extra.get("end_state")
    return state if isinstance(state, dict) else None


def end_state_block(episode: Any | None) -> str:
    """上一集集末契约渲染成提示词块;无契约 → 空串。

    与小说侧 handoff 同构:原文供语感,契约供事实。剧本此前只有原文,
    模型要从散文里推断"现在几点、谁还在场、什么事没完"——推断错无兜底。
    """
    state = end_state_of(episode) if episode is not None else None
    if not state:
        return ""
    lines: list[str] = []
    if str(state.get("in_story_time") or "").strip():
        lines.append(f"  剧内时间:{state['in_story_time']}")
    if str(state.get("location") or "").strip():
        lines.append(f"  地点:{state['location']}")
    on_stage = [str(x).strip() for x in (state.get("on_stage") or []) if str(x).strip()]
    if on_stage:
        lines.append(f"  仍在场:{'、'.join(on_stage)}")
    for c in (state.get("character_states") or [])[:6]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        bits = [str(c.get(k) or "").strip() for k in ("state", "doing")]
        detail = " · ".join(b for b in bits if b)
        lines.append(f"  {name}:{detail}" if detail else f"  {name}")
    threads = [str(x).strip() for x in (state.get("open_threads") or []) if str(x).strip()]
    if threads:
        lines.append("  未了线索:" + ";".join(threads[:6]))
    if not lines:
        return ""
    return (
        "【上一集集末状态(硬约束:本集开场必须由此接住,"
        "未了线索要在本集接住或兑现)】\n" + "\n".join(lines) + "\n"
    )


def store_end_state(episode: Any, state: dict | None, error: str = "") -> None:
    """落集末契约。提取失败时记 failed + 原因,供界面提示「可重提」。"""
    extra = dict(episode.extra) if isinstance(episode.extra, dict) else {}
    if state:
        extra["end_state"] = state
        extra["end_state_status"] = "ok"
        extra["end_state_error"] = ""
    else:
        extra["end_state_status"] = "failed"
        extra["end_state_error"] = str(error)[:300]
    episode.extra = extra


# =============== 版本快照(存 episode.extra["versions"])===============

_MAX_VERSIONS = 10  # 每集最多留这么多版历史


def push_version(episode: Any, source: str = "generated") -> int:
    """覆盖正文前把当前正文存一版;无旧正文则跳过。返回新版本号。"""
    old = (episode.content or "").strip()
    if not old:
        return 0
    extra = dict(episode.extra) if isinstance(episode.extra, dict) else {}
    versions = list(extra.get("versions") or [])
    version = (versions[-1]["version"] + 1) if versions else 1
    versions.append({
        "version": version,
        "content": old,
        "word_count": len(old),
        "source": source,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    extra["versions"] = versions[-_MAX_VERSIONS:]
    episode.extra = extra
    return version


def version_list(episode: Any) -> list[dict]:
    """历史版本(最新在前)。坏数据静默跳过,历史不该拖垮正本读取。"""
    extra = episode.extra if isinstance(episode.extra, dict) else {}
    rows = [
        v for v in (extra.get("versions") or [])
        if isinstance(v, dict) and int(v.get("version") or 0) > 0
    ]
    rows.sort(key=lambda v: int(v.get("version") or 0), reverse=True)
    return [
        {
            "version": int(v.get("version") or 0),
            "word_count": int(v.get("word_count") or 0),
            "source": str(v.get("source") or ""),
            "saved_at": str(v.get("saved_at") or ""),
            "content": str(v.get("content") or ""),
        }
        for v in rows
    ]


def find_version(episode: Any, version: int) -> dict | None:
    """按版本号取一版;不存在 → None。"""
    for v in version_list(episode):
        if v["version"] == version:
            return v
    return None


def episode_dict(row: Any) -> dict:
    """行 → dict(API 响应共用)。"""
    extra = row.extra if isinstance(row.extra, dict) else {}
    return {
        "id": row.id,
        "script_id": row.script_id,
        "episode_number": row.episode_number,
        "title": row.title,
        "synopsis": row.synopsis,
        "opening_hook": row.opening_hook,
        "ending_hook": row.ending_hook,
        "status": row.status,
        "status_cn": STATUS_CN.get(row.status, row.status),
        "content": row.content,
        "word_count": row.word_count,
        "end_state_status": extra.get("end_state_status") or "",
        "versions": len(extra.get("versions") or []),
    }
