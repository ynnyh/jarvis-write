# app/engines/script/generate.py
# -*- coding: utf-8 -*-
"""剧本生成编排:分集大纲 → 单集剧本(格式门禁 + 重试)→ 集末交接契约。

与路由层解耦:适配器由调用方注入(便于测试替换,也让引擎不依赖 llm.router)。
引擎只抛 ScriptError(业务性错误,信息可直接上屏),HTTP 状态码归路由管。

相对旧实现(一次调用、返回文本直接落库)的三处加固:
  ① 格式门禁 + 整发重试:只挡「没写成」(空壳/截断/JSON/无场景标题),
     不评判文笔;崩坏时重试一次,仍不成才报错——中转截断是常态不是意外。
  ② 集末交接契约:写完本集后低温度提取「落幕那一刻」的状态(时间/地点/
     在场/未了线索),下一集 P0 注入。小说侧的「睡着又发呆」问题,剧本线
     此前完全没有对应的机制。
  ③ 版本快照:覆盖正文前存一版到 extra,手改/重写不再丢上一版。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.engines.common import ask_llm_json
from app.engines.script.common import (
    END_STATE_TAIL_CHARS,
    MAX_CONTENT_CHARS,
    PREV_TAIL_CHARS,
    end_state_block,
    push_version,
    store_end_state,
    strip_meta,
    validate_script_content,
)
from app.prompts.script import (
    SCRIPT_END_STATE_PROMPT,
    SCRIPT_EPISODE_PROMPT,
    SCRIPT_OUTLINE_PROMPT,
)

logger = logging.getLogger("jarvis-write.script")

_ATTEMPTS = 2  # 输出崩坏时整发重试一次


class ScriptError(ValueError):
    """剧本工坊的业务性错误(信息直接上屏)。"""


async def generate_outline(adapter, script) -> list[dict]:
    """AI 生成分集大纲(纯生成,清旧集与落库归调用方)。"""
    seed_block = f"【灵感种子】{script.logline}\n" if (script.logline or "").strip() else ""
    prompt = SCRIPT_OUTLINE_PROMPT.format(
        title=script.title, genre=script.genre or "剧情",
        logline=script.logline or "(未填)",
        target_episodes=script.target_episodes, seed_block=seed_block,
    )
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise ScriptError(f"分集大纲生成失败: {exc}") from exc
    data, err = _parse(raw)
    if err:
        raise ScriptError(f"分集大纲生成失败:{err}")
    episodes = data.get("episodes") or []
    if not episodes:
        raise ScriptError("模型没有返回可用的大纲,请重试。")
    return episodes


async def generate_episode(
    db: Session, script, episode, draft_adapter, state_adapter=None,
    *, extra_direction: str = "",
) -> None:
    """生成单集剧本并落库(含集末契约)。失败抛 ScriptError,不动数据库。"""
    prev = _previous_episode(db, script.id, episode.episode_number)
    prompt = SCRIPT_EPISODE_PROMPT.format(
        n=episode.episode_number,
        title=episode.title or script.title,
        genre=script.genre or "剧情",
        synopsis=episode.synopsis or episode.title,
        opening_hook=episode.opening_hook,
        ending_hook=episode.ending_hook,
        prev_state_block=end_state_block(prev),
        prev_block=_prev_tail_block(prev),
        memo_block=_memo_block(script),
        extra_block=_extra_block(extra_direction),
    )

    content, last_err = "", "模型返回空内容"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            raw = await draft_adapter.ask(prompt)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("第 %d 集剧本生成第 %d/%d 次调用失败:%s",
                           episode.episode_number, attempt, _ATTEMPTS, last_err)
            continue
        candidate = strip_meta(raw)
        ok, why = validate_script_content(candidate)
        if ok:
            content = candidate[:MAX_CONTENT_CHARS]
            break
        last_err = why
        logger.warning("第 %d 集剧本第 %d/%d 次输出不可用:%s",
                       episode.episode_number, attempt, _ATTEMPTS, why)
    if not content:
        raise ScriptError(f"剧本生成失败:{last_err}")

    # 覆盖前存一版(手改/重写可回溯)
    push_version(episode, source="generated")
    episode.content = content
    episode.word_count = len(content)
    episode.status = "drafted"

    # 集末契约:给下一集用的衔接锚。失败只降级,不推翻已经写好的正本。
    if state_adapter is not None:
        await _store_end_state(episode, state_adapter)
    db.commit()


async def _store_end_state(episode, state_adapter) -> None:
    """提取并落集末契约;失败记 failed(界面可提示重提),不影响本集正文。"""
    n = episode.episode_number
    tail = (episode.content or "")[-END_STATE_TAIL_CHARS:]
    prompt = SCRIPT_END_STATE_PROMPT.format(n=n, tail=tail)
    # 契约是衔接增强,不值得为它烧满重试预算:整发重发 1 次 + 续写抢救 1 次封顶。
    try:
        data, err = await ask_llm_json(
            state_adapter, prompt, label=f"第 {n} 集集末契约",
            attempts=1, continue_attempts=1,
        )
    except Exception as exc:  # noqa: BLE001 — 契约是衔接增强,不该拖垮正文
        err, data = str(exc), {}
    if err or not data:
        store_end_state(episode, None, err or "模型返回空内容")
        logger.warning("第 %d 集集末契约提取失败:%s", n, err or "空内容")
        return
    store_end_state(episode, data)
    logger.info("第 %d 集集末契约已提取", n)


def _previous_episode(db: Session, script_id: int, n: int):
    from app.db.models import ScriptEpisode

    return (
        db.query(ScriptEpisode)
        .filter(ScriptEpisode.script_id == script_id, ScriptEpisode.episode_number < n)
        .order_by(ScriptEpisode.episode_number.desc())
        .first()
    )


def _prev_tail_block(prev) -> str:
    if prev is None or not (prev.content or "").strip():
        return ""
    return f"【上一集结尾(衔接用,只取最后 {PREV_TAIL_CHARS} 字)】\n{prev.content[-PREV_TAIL_CHARS:]}\n"


def _memo_block(script) -> str:
    memo = (script.style_memo or "").strip()
    return f"【剧本文风备忘】\n{memo}\n" if memo else ""


def _extra_block(extra_direction: str) -> str:
    extra = (extra_direction or "").strip()
    return f"【用户补充方向(最高优先级)】\n{extra}\n" if extra else ""


def _parse(raw: str) -> tuple[dict, str | None]:
    """大纲/改编这类 JSON 输出用宽容解析(带续写保险在 ask_llm_json 里)。"""
    from app.engines.common import parse_llm_json_checked

    return parse_llm_json_checked(raw)
