# app/engines/drama/quality.py
# -*- coding: utf-8 -*-
"""漫剧线的质量件:格式门禁 / 集末交接契约 / 版本快照(docs/15 §5.2)。

漫剧线是**代码量最大(近 4000 行)的一环,却也是最空的一环**:
2026-09-10 审查发现它此前只在部分环节查「输出非空」,没有格式门禁、没有版本
快照、零一致性校验。剧本线同一天补上了三件套(engines/script/),漫剧线照抄
思路即可——但**不能照搬实现**:

  · 剧本线校验的是 Fountain 风格散文(靠「内景/外景」场景标题行判格式),
    漫剧线产出的是 **JSON 台词稿**(`{"lines": [{speaker,text,action}]}`),
    判据完全不同(见 `validate_drama_script`)。
  · 剧本线把契约/快照存 `ScriptEpisode.extra`;漫剧线没有 extra 列,但
    `DramaEpisode.script` 本身就是 JSON 列——快照挂在它的 `_versions` 键下,
    免去迁移(与剧本线同一取舍理由:集数量级小,JSON 够用)。

于是本模块只做三件确定性的事(零 LLM,除契约提取外):
  ① `validate_drama_script`:挡「没写成」——空壳/JSON 解析失败/台词条数过少/
     说话人全空。**不评判文笔**(那不是确定性代码该管的)。
  ② 集末契约(存 `script["_end_state"]`):写完本集后提取「落幕那一刻」的
     时间/地点/在场/未了线索,下一集 prompt 注入。小说侧与剧本线都有这个
     机制,漫剧线此前只靠 `_prev_block` 传上一集的 cliffhanger 一句话。
  ③ 版本快照(存 `script["_versions"]`):覆盖剧本前存一版,重写不再丢上一版。

边界:本模块只提供**判据与存取**,不做编排(重试与调用顺序归 script.py);
这样门禁能被单测直接喂数据验证,不必起 LLM。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("jarvis-write.drama.quality")

# =============== 篇幅与截取 ===============

MIN_LINES = 4          # 低于此条数视为输出被截断/空壳(一集剧本至少几句台词)
PREV_TAIL_LINES = 12   # 上一集结尾注入下一集的台词条数(衔接语感)
END_STATE_TAIL_LINES = 16  # 提取集末契约时看的结尾台词条数

# =============== 格式门禁 ===============

def validate_drama_script(data: Any) -> tuple[bool, str]:
    """漫剧台词稿格式门禁。返回 (是否可用, 失败原因)。

    与剧本线 `validate_script_content` 同**思路**(只挡明显没写成,不管文笔)、
    不同**判据**——漫剧产出是结构化的,判据落在结构上:

      · `data` 必须是 dict(JSON 解析失败时调用方传的就是 None/空);
      · `lines` 必须是非空列表,且条数 ≥ MIN_LINES(只有 1-2 条 = 被截断);
      · 至少要有一条**有说话人的**台词——全「旁白」且无 text 说明模型没写戏;
      · 每条 line 必须有非空 text。

    `narration`(口播解说)模式下旁白为主是正常的,故不强制对白数量。
    """
    if not isinstance(data, dict):
        return False, "输出不是 JSON 对象(多半被截断或模型没按格式写)"
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return False, "缺少 lines 字段或台词为空"
    usable = [
        ln for ln in raw_lines
        if isinstance(ln, dict) and str(ln.get("text") or "").strip()
    ]
    if not usable:
        return False, "所有台词行的 text 都是空的"
    if len(usable) < MIN_LINES:
        return False, (
            f"只写出了 {len(usable)} 条台词(低于 {MIN_LINES} 条),"
            "疑似输出被截断"
        )
    speakers = {
        str(ln.get("speaker") or "").strip()
        for ln in usable
    }
    speakers.discard("")
    if not speakers:
        return False, "所有台词都没有说话人(模型没写戏,只是复述)"
    return True, ""


# =============== 集末交接契约(存 script["_end_state"])===============

def end_state_of(episode: Any) -> dict | None:
    """读取集末契约;提取失败或未提取 → None(回退现状,不报错)。"""
    script = episode.script if isinstance(episode.script, dict) else {}
    holder = script.get("_end_state") if isinstance(script.get("_end_state"), dict) else {}
    if holder.get("status") != "ok":
        return None
    state = holder.get("state")
    return state if isinstance(state, dict) else None


def store_end_state(episode: Any, state: dict | None, error: str = "") -> None:
    """落集末契约。提取失败时记 failed + 原因(界面可提示「可重提」)。"""
    script = dict(episode.script) if isinstance(episode.script, dict) else {}
    if state:
        script["_end_state"] = {"status": "ok", "state": state, "error": ""}
    else:
        script["_end_state"] = {"status": "failed", "state": None, "error": str(error)[:300]}
    episode.script = script


def end_state_block(episode: Any | None) -> str:
    """上一集集末契约 → prompt 块;无契约 → 空串(整块省略)。"""
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


def tail_lines_text(episode: Any, limit: int = END_STATE_TAIL_LINES) -> str:
    """末尾若干条台词 → 纯文本(提取集末契约的输入)。"""
    script = episode.script if isinstance(episode.script, dict) else {}
    lines = script.get("lines") if isinstance(script.get("lines"), list) else []
    out: list[str] = []
    for ln in lines[-limit:]:
        if not isinstance(ln, dict):
            continue
        who = str(ln.get("speaker") or "旁白").strip()
        text = str(ln.get("text") or "").strip()
        action = str(ln.get("action") or "").strip()
        if not text:
            continue
        seg = f"{who}:{text}"
        if action:
            seg += f"({action})"
        out.append(seg)
    return "\n".join(out)


# =============== 版本快照(存 script["_versions"])===============

_MAX_VERSIONS = 10  # 每集最多留这么多版历史


def push_version(episode: Any, source: str = "generated") -> int:
    """覆盖剧本前把当前剧本存一版;无旧剧本则跳过。返回新版本号。

    与剧本线 `push_version` 同语义,但快照内容取整份 `script`(漫剧是结构化
    台词稿,不能只存字符串正文)。
    """
    old = episode.script if isinstance(episode.script, dict) else {}
    old_lines = old.get("lines") if isinstance(old.get("lines"), list) else []
    if not old_lines:
        return 0
    script = dict(old)
    versions = list(script.get("_versions") or [])
    version = (versions[-1].get("version", 0) + 1) if versions else 1
    versions.append({
        "version": version,
        "lines": old_lines,
        "synopsis": str(old.get("synopsis") or ""),
        "line_count": len(old_lines),
        "source": source,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    script["_versions"] = versions[-_MAX_VERSIONS:]
    episode.script = script
    return version


def version_list(episode: Any) -> list[dict]:
    """历史版本(最新在前)。坏数据静默跳过。"""
    script = episode.script if isinstance(episode.script, dict) else {}
    rows = [
        v for v in (script.get("_versions") or [])
        if isinstance(v, dict) and int(v.get("version") or 0) > 0
    ]
    rows.sort(key=lambda v: int(v.get("version") or 0), reverse=True)
    return [
        {
            "version": int(v.get("version") or 0),
            "line_count": int(v.get("line_count") or 0),
            "source": str(v.get("source") or ""),
            "saved_at": str(v.get("saved_at") or ""),
            "synopsis": str(v.get("synopsis") or ""),
            "lines": v.get("lines") if isinstance(v.get("lines"), list) else [],
        }
        for v in rows
    ]


def find_version(episode: Any, version: int) -> dict | None:
    for v in version_list(episode):
        if v["version"] == version:
            return v
    return None
