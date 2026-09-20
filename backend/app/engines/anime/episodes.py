# app/engines/anime/episodes.py
# -*- coding: utf-8 -*-
"""动画短剧集引擎:命题 → 三梗纲三选一 → 分镜 → 整集分段精准提示词。

一条龙都吃「卡司是硬规则」:出梗/分镜/提示词只用既定卡司,模板里白纸黑字
写死「不许新增有名有姓的角色」。整集提示词与漫剧/宣传片同走分段式
(超过外部模型单次上限必然切段,复用镜头边界贪心),文档头也复用同一份;
差异只在精度口径——逐镜五要素、禁含糊词、每段字数只设下限。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.engines.anime.common import (
    MAX_SHOTS,
    AnimeError,
    cast_block,
    episode_dict,
    genre_of,
    merge_cast_locked,
    norm_cast,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.segments import group_by_limit
from app.engines.media.text import strip_fences
from app.engines.media.directions import direction_directive
from app.llm.router import Task, get_adapter_for
from app.prompts.anime import (
    ANIME_CAST_PROMPT,
    ANIME_EPISODE_SUGGEST_PROMPT,
    ANIME_PREMISE_SUGGEST_PROMPT,
    ANIME_SEGMENT_PROMPT_TEMPLATE,
    ANIME_SHOTS_PROMPT,
    ANIME_SYNOPSIS_CHAT_PROMPT,
    ANIME_TAKES_PROMPT,
)
from app.prompts.film_prompt import segmented_doc_header

logger = logging.getLogger(__name__)

_ATTEMPTS = 2  # 空输出/数量不对/解析失败,整发重试一次


def _hints_block(style_cn: str) -> str:
    style = (style_cn or "").strip()
    if not style:
        return ""
    return f"【画风锚(必须自然融入)】{style}\n"


# =============== 卡司 ===============


async def generate_cast(db: Session, series, progress=lambda s: None) -> list[dict]:
    """一句话设定 → 卡司提案落库;locked 角色原样保留(按名字),其余换新。"""
    g = genre_of(series.genre)
    progress("AI 正在设计固定卡司…")
    prompt = ANIME_CAST_PROMPT.format(
        genre_label=g["label"],
        framing=g["framing"],
        premise=(series.premise or "").strip() or "未填设定,按类型自行设计一组反差鲜明的卡司",
        direction_directive=direction_directive(series.direction),
        hints_block=_hints_block(series.style_cn),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_CAST, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            fresh = norm_cast(data.get("cast"))
            merged = merge_cast_locked(list(series.cast or []), fresh)
            # 拷贝-改-赋回:JSON 列原地改 SQLAlchemy 不认,commit 会空转
            series.cast = merged
            series.status = "cast_ready"
            db.commit()
            return merged
        except AnimeError:
            raise
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("卡司生成第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"卡司没设计好({last_err}),再点一次试试。")


def save_cast(series, cast: list) -> list[dict]:
    """手改保存:整卡替换(前端把 locked 一并带上,归一不丢)。"""
    merged = merge_cast_locked([], norm_cast(cast))
    series.cast = merged
    series.status = "cast_ready" if series.status == "cast_empty" else series.status
    return merged


# =============== 没灵感?AI 出点子(系列设定 / 下一集命题) ===============


def _norm_premises(value: object) -> list[str]:
    """点子归一:恰好 3 条、每条一句话(≤60 字)、互不相同;空壳报错让引擎重试。"""
    if not isinstance(value, list) or not value:
        raise ValueError("模型没有返回点子数组")
    out: list[str] = []
    for p in value[:6]:
        text = str(p or "").strip()[:60]
        if text and text not in out:
            out.append(text)
    if len(out) < 3:
        raise ValueError(f"点子要 3 个互不相同的,模型只给了能用的 {len(out)} 个")
    return out


async def suggest_series_premises(genre: str, progress=lambda s: None) -> list[str]:
    """系列设定点子三选一:没灵感也能开工,选中后仍走正常确认流。"""
    g = genre_of(genre)
    progress("AI 正在出系列设定点子…")
    prompt = ANIME_PREMISE_SUGGEST_PROMPT.format(
        genre_label=g["label"], framing=g["framing"], beats=g["beats"],
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_SUGGEST, timeout=300)
            return _norm_premises(parse_llm_json(await adapter.ask(prompt)).get("premises"))
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("设定点子第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"点子没出好({last_err}),再点一次试试。")


async def suggest_episode_premises(
    series, used: list[str] | None = None, progress=lambda s: None
) -> list[str]:
    """下一集点子三选一:贴卡司、贴类型节奏,避开已经用过的集命题。"""
    if not (series.cast or []):
        raise AnimeError("这个系列还没有卡司:先在系列工作台把班底定下来。")
    g = genre_of(series.genre)
    used_lines = [str(u or "").strip() for u in (used or [])]
    used_lines = [u for u in used_lines if u]
    used_block = "\n".join(f"- {u}" for u in used_lines) if used_lines else "(还没有已用命题)"
    progress("AI 正在出下一集点子…")
    prompt = ANIME_EPISODE_SUGGEST_PROMPT.format(
        genre_label=g["label"],
        framing=g["framing"],
        beats=g["beats"],
        premise=(series.premise or "").strip() or "(未填,按卡司与类型自拟)",
        cast_block=cast_block(series.cast),
        used_block=used_block,
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_SUGGEST, timeout=300)
            return _norm_premises(parse_llm_json(await adapter.ask(prompt)).get("premises"))
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("集点子第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"点子没出好({last_err}),再点一次试试。")


# =============== 简介聊天(对话式确认流) ===============

CHAT_USER_MAX = 500      # 单条用户消息上限
CHAT_KEEP = 40           # 线程最多保留条数(超出丢最旧的)


def _chat_block(chat: list[dict]) -> str:
    if not chat:
        return "(空,这是第一轮)"
    return "\n".join(
        f"{'用户' if m.get('role') == 'user' else 'AI'}:{str(m.get('content') or '')[:800]}"
        for m in chat[-CHAT_KEEP:]
    )


async def anime_chat(
    db: Session, series, episode, message: str, progress=lambda s: None
) -> dict:
    """用户一句话 → AI 接住并补充完善,返回当前完整版简介;线程与简介草稿落库。

    确认是另一拍(confirm_synopsis):聊得再好,用户没点头,分镜按钮就不亮。
    """
    if not (series.cast or []):
        raise AnimeError("这个系列还没有卡司:先把班底定下来再聊剧情。")
    g = genre_of(series.genre)
    clean = (message or "").strip()[:CHAT_USER_MAX]
    history = [m for m in (episode.chat or []) if isinstance(m, dict)]
    thread = history + [{"role": "user", "content": clean}] if clean else list(history)
    progress("AI 正在接着你的点子完善简介…")
    prompt = ANIME_SYNOPSIS_CHAT_PROMPT.format(
        genre_label=g["label"],
        framing=g["framing"],
        beats=g["beats"],
        premise=(series.premise or "").strip() or "(未填)",
        premise_line=(episode.premise or "").strip() or "(空,按类型与卡司自拟)",
        cast_block=cast_block(series.cast),
        chat_block=_chat_block(thread),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_CHAT, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            reply = str(data.get("reply") or "").strip()[:1000]
            synopsis = str(data.get("synopsis") or "").strip()[:2000]
            if not reply or not synopsis:
                raise ValueError("模型回了空 reply/synopsis(空壳)")
            # 拷贝-改-赋回:JSON 列原地改 SQLAlchemy 不认,commit 会空转
            thread_out = (thread + [{"role": "assistant", "content": reply}])[-CHAT_KEEP:]
            episode.chat = thread_out
            episode.synopsis = synopsis
            episode.synopsis_ok = 0  # 有新草稿,旧确认作废;重新拍板才往下走
            db.commit()
            return {"reply": reply, "synopsis": synopsis, "episode": episode_dict(episode)}
        except AnimeError:
            raise
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("简介聊天第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"这轮没接住({last_err}),再发一次试试。")


def confirm_synopsis(episode, synopsis: str | None = None) -> dict:
    """用户拍板:简介定稿,分镜解锁;改简介/换梗纲都会清掉下游产物。"""
    text = (synopsis if synopsis is not None else episode.synopsis or "").strip()[:2000]
    if not text:
        raise AnimeError("还没有简介可确认:先聊一轮,或让 AI 出三个梗纲挑一个。")
    episode.synopsis = text
    episode.synopsis_ok = 1
    episode.shots = []
    episode.film_prompt = ""
    episode.status = "synopsis_ready"
    return episode_dict(episode)


# =============== 梗纲三选一(没点子时的捷径) ===============


async def gen_takes(db: Session, series, episode, progress=lambda s: None) -> dict:
    """命题 → 3 个梗纲(JSON),存 takes;不覆盖已选定的梗纲(先清 chosen 才许重出)。"""
    if not (series.cast or []):
        raise AnimeError("这个系列还没有卡司:先在系列工作台把班底定下来。")
    g = genre_of(series.genre)
    premise_line = (episode.premise or "").strip() or "(空,按类型与卡司自拟最适合的情境)"
    progress("AI 正在出三个梗纲…")
    prompt = ANIME_TAKES_PROMPT.format(
        genre_label=g["label"],
        framing=g["framing"],
        beats=g["beats"],
        premise=(series.premise or "").strip() or "(未填)",
        premise_line=premise_line,
        cast_block=cast_block(series.cast),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_TAKES, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            takes = _norm_takes(data.get("takes"))
            episode.takes = takes
            episode.chosen = -1
            episode.title = ""
            episode.shots = []
            episode.film_prompt = ""
            # 已确认的简介不动(梗纲只是没点子时的捷径);没确认过则状态停在梗纲已出
            episode.status = "synopsis_ready" if episode.synopsis_ok else "takes_ready"
            db.commit()
            return episode_dict(episode)
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("梗纲生成第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"梗纲没出好({last_err}),再点一次试试。")


def _norm_takes(takes: object) -> list[dict]:
    """梗纲归一:恰好 3 个,每拍一句话;空壳直接报错(让引擎重试)。"""
    if not isinstance(takes, list) or not takes:
        raise ValueError("模型没有返回梗纲数组")
    out = []
    for t in takes[:3]:
        if not isinstance(t, dict):
            continue
        beats = t.get("beats")
        beats_list = [
            str(b).strip()[:120] for b in beats if str(b or "").strip()
        ] if isinstance(beats, list) else []
        logline = str(t.get("logline") or "").strip()
        if not logline or not beats_list:
            raise ValueError("梗纲缺 logline 或 beats(空壳)")
        out.append({
            "logline": logline[:200],
            "beats": beats_list,
            "punchline": str(t.get("punchline") or "").strip()[:200],
            "highlight": str(t.get("highlight") or "").strip()[:200],
        })
    if len(out) != 3:
        raise ValueError(f"梗纲要 3 个,模型只给了能用的 {len(out)} 个")
    return out


def _take_synopsis(take: dict) -> str:
    """梗纲 → 简介全文(选定梗纲即确认这条简介,两条入口在此汇合)。"""
    parts = [str(take.get("logline") or "").strip()]
    beats = take.get("beats") or []
    if beats:
        parts.append("节奏:" + ";".join(str(b).strip() for b in beats if str(b).strip()))
    punch = str(take.get("punchline") or "").strip()
    if punch:
        parts.append(f"落点:{punch}")
    return "\n".join(p for p in parts if p)[:2000]


def pick_take(episode, index: int) -> dict:
    """选定梗纲 = 确认这条简介(用户拍板);清空下游(分镜/提示词跟着作废)。"""
    if not (0 <= index < len(episode.takes or [])):
        raise AnimeError("梗纲序号不对:没有这一条。")
    take = episode.takes[index]
    episode.chosen = index
    episode.synopsis = _take_synopsis(take)
    episode.synopsis_ok = 1
    episode.shots = []
    episode.film_prompt = ""
    episode.status = "synopsis_ready"
    return episode_dict(episode)


# =============== 分镜 ===============


async def gen_shots(db: Session, series, episode, progress=lambda s: None) -> dict:
    """确认后的简介 → 分镜(每镜 2-5 秒,台词动作全开),存 shots。"""
    if not episode.synopsis_ok or not (episode.synopsis or "").strip():
        raise AnimeError("还没确认简介:和 AI 聊完点「确认简介」,或选一个梗纲,再展开分镜。")
    g = genre_of(series.genre)
    total_s = int(series.episode_s or 60)
    shot_count = max(10, round(total_s / 3.5))
    progress("AI 正在把简介展开成分镜…")
    prompt = ANIME_SHOTS_PROMPT.format(
        genre_label=g["label"],
        framing=g["framing"],
        direction_directive=direction_directive(series.direction),
        cast_block=cast_block(series.cast),
        title=(episode.title or series.title or "本集").strip()[:40],
        synopsis_block=episode.synopsis.strip(),
        total_s=total_s,
        shot_count=shot_count,
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.ANIME_SHOTS, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            shots = _norm_shots(data.get("shots"), total_s)
            title = str(data.get("title") or "").strip()[:60]
            if title:
                episode.title = title
            episode.shots = shots
            episode.film_prompt = ""  # 分镜变了,旧提示词作废
            episode.status = "shots_ready"
            db.commit()
            return episode_dict(episode)
        except AnimeError:
            raise
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("分镜生成第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise AnimeError(f"分镜没出好({last_err}),再点一次试试。")


def _norm_shots(shots: object, total_s: int) -> list[dict]:
    """分镜归一:seq 重排、时长钳到 2-5 秒、字段裁剪;总时长偏出 ±8 秒算不合格(重试)。"""
    if not isinstance(shots, list) or not shots:
        raise ValueError("模型没有返回分镜数组")
    out = []
    for i, s in enumerate(shots[:MAX_SHOTS], 1):
        if not isinstance(s, dict):
            continue
        try:
            dur = int(s.get("duration_s") or 0)
        except (TypeError, ValueError):
            dur = 0
        dur = min(5, max(2, dur or 3))
        chars = s.get("characters")
        out.append({
            "seq": i,
            "shot_type": str(s.get("shot_type") or "中景").strip()[:20],
            "camera": str(s.get("camera") or "固定").strip()[:40],
            "duration_s": dur,
            "action_desc": str(s.get("action_desc") or "").strip()[:300],
            "dialogue": str(s.get("dialogue") or "").strip()[:200],
            "speaker": str(s.get("speaker") or "").strip()[:30],
            "characters": [
                str(c).strip()[:30] for c in chars if str(c or "").strip()
            ] if isinstance(chars, list) else [],
            "sfx": str(s.get("sfx") or "").strip()[:80],
        })
    if len(out) < 5:
        raise ValueError(f"分镜太少({len(out)} 镜),不像一集完整的片子")
    got = sum(s["duration_s"] for s in out)
    if abs(got - total_s) > 8:
        raise ValueError(f"分镜总时长 {got} 秒,离目标 {total_s} 秒太远")
    return out


def save_shots(episode, shots: list) -> list[dict]:
    """手改分镜保存:整卡替换 + 重排 seq;提示词随之作废(改了分镜就得重出)。"""
    if not isinstance(shots, list):
        raise AnimeError("分镜格式不对:应该是一个镜头数组。")
    out = []
    for i, s in enumerate(shots[:MAX_SHOTS], 1):
        if not isinstance(s, dict):
            continue
        try:
            dur = int(s.get("duration_s") or 3)
        except (TypeError, ValueError):
            dur = 3
        chars = s.get("characters")
        out.append({
            "seq": i,
            "shot_type": str(s.get("shot_type") or "中景").strip()[:20],
            "camera": str(s.get("camera") or "固定").strip()[:40],
            "duration_s": min(9, max(1, dur)),
            "action_desc": str(s.get("action_desc") or "").strip()[:300],
            "dialogue": str(s.get("dialogue") or "").strip()[:200],
            "speaker": str(s.get("speaker") or "").strip()[:30],
            "characters": [
                str(c).strip()[:30] for c in chars if str(c or "").strip()
            ] if isinstance(chars, list) else [],
            "sfx": str(s.get("sfx") or "").strip()[:80],
        })
    if not out:
        raise AnimeError("一个可用镜头都没有:至少要留一镜。")
    episode.shots = out
    episode.film_prompt = ""
    episode.status = "shots_ready"
    return out


# =============== 整集分段提示词 ===============


def _segments_block(groups: list[list[dict]]) -> str:
    """分段计划原料:每段一行一镜(时间码累计),台词带说话人,音效点名。"""
    rows = []
    t = 0
    for i, group in enumerate(groups, 1):
        start, end = t, t + sum(s["duration_s"] for s in group)
        t = end
        lines = [f"【第{i}段|{start}—{end}秒】"]
        for s in group:
            line = (
                f"  - {s['seq']}|{s['shot_type']}|运镜:{s['camera']}|{s['duration_s']}秒"
                f"|画面:{s['action_desc'] or '未写'}"
            )
            if s["dialogue"]:
                sp = f"({s['speaker']})" if s["speaker"] else ""
                line += f"|台词{sp}:{s['dialogue']}"
            if s["sfx"]:
                line += f"|音效:{s['sfx']}"
            lines.append(line)
        rows.append("\n".join(lines))
    return "\n".join(rows)


async def build_film_prompt(
    db: Session, series, episode, progress=lambda s: None, segment_s: int = 15
) -> dict:
    """分镜 → 整集分段精准提示词文档,整体覆盖 episode.film_prompt。"""
    shots = [s for s in (episode.shots or []) if isinstance(s, dict)]
    if not shots:
        raise AnimeError("这集还没有分镜:先三选一梗纲并展开分镜,再来出提示词。")
    if segment_s not in (15, 30):
        raise AnimeError("单段时长只支持 15 / 30 秒。")

    g = genre_of(series.genre)
    groups = group_by_limit(shots, segment_s)
    total_s = sum(int(s.get("duration_s") or 0) for s in shots)
    # 每段字数下限(只设下限,上不封顶):15s 段 ≥400 字,30s 段 ≥800 字
    seg_floor = "400 字" if segment_s <= 15 else "800 字"

    progress(f"AI 正在把 {len(groups)} 段分镜组装成分段提示词…")
    adapter = get_adapter_for(Task.ANIME_PROMPT, timeout=300)
    prompt = ANIME_SEGMENT_PROMPT_TEMPLATE.format(
        title=(episode.title or series.title or "动画短剧").strip()[:40],
        genre_label=g["label"],
        total_s=total_s,
        seg_count=len(groups),
        segment_s=segment_s,
        ratio="9:16 竖屏",
        framing=g["framing"],
        style_block=series.style_cn or direction_directive(series.direction),
        cast_block=cast_block(norm_cast(series.cast)) if series.cast else "(无卡司档案)",
        segments_block=_segments_block(groups),
        seg_floor=seg_floor,
    )
    raw = await adapter.ask(prompt)
    text = strip_fences(raw)
    if not text:
        raise AnimeError("模型返回了空内容,请重试一次。")
    episode.film_prompt = segmented_doc_header(len(groups), segment_s) + text
    episode.status = "prompted"
    db.commit()
    return {"chars": len(episode.film_prompt), "segments": len(groups)}
