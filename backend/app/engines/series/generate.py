# app/engines/series/generate.py
# -*- coding: utf-8 -*-
"""角色系列短片生成:①AI 代写定妆描述,②定妆+剧情 → 单集成片提示词。

与三本子工坊的批产完全不同的两个取舍:
- **轻量单条**:不走「①风格卡+三切入 ②三发并行展开」两段式——主角档案已把
  画风与形象定死,每集只剩「剧情展开成一条提示词」一件 LLM 的事,一发直出;
- **定妆逐字注入**:look 是全系列一致性的锚,提示词模板里以最高优先级铁律
  要求逐字采用;引擎不再做关键词核对(用户自己肉眼把关,核对误报反而添乱)。

字数不设下限(用户明确:允许超过五百或一千,但一两百字也收——篇幅按剧情
密度自由决定)。守卫只挡**空壳**:prompt_cn/look 为空串说明模型没写成
(截断/解析失败),整发重试一次;写出来了,长短都直接收。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import SeriesCharacter, SeriesEpisode
from app.engines.consistency.extractor import parse_llm_json
from app.engines.media.anchors import merge_negative
from app.engines.media.directions import direction_directive
from app.engines.series.common import (
    BRIEF_MAX,
    LOOK_MAX,
    NAME_MAX,
    PLOT_MAX,
    norm_output,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.series import (
    SERIES_EPISODE_PROMPT,
    SERIES_IDEA_PROMPT,
    SERIES_LOOK_PROMPT,
    SERIES_PLOT_PROMPT,
)

logger = logging.getLogger(__name__)

_ATTEMPTS = 2  # 空输出/字数不达标/解析失败,整发重试一次


class SeriesError(ValueError):
    """系列工坊的业务性错误(信息直接上屏)。"""


def _hints_block(style_hints: str) -> str:
    hints = (style_hints or "").strip()
    if not hints:
        return ""
    return f"【氛围关键词(必须自然融入画面与光线)】{hints}\n"


# =============== 没灵感?AI 出点子(主角概念 / 下一集剧情) ===============


async def suggest_character_ideas(direction: str, style_hints: str = "") -> list[dict]:
    """没灵感:出 3 个固定主角点子(name+brief),不落库——选中由前端回填表单,
    之后照常「AI 代写定妆 → 建主角」。"""
    prompt = SERIES_IDEA_PROMPT.format(
        direction_directive=direction_directive(direction),
        hints_block=_hints_block(style_hints),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.SERIES_IDEA, timeout=300)
            ideas = parse_llm_json(await adapter.ask(prompt)).get("ideas")
            out: list[dict] = []
            if isinstance(ideas, list):
                for it in ideas[:6]:
                    if not isinstance(it, dict):
                        continue
                    name = str(it.get("name") or "").strip()[:NAME_MAX]
                    brief = str(it.get("brief") or "").strip()[:BRIEF_MAX]
                    if name and brief and all(name != x["name"] for x in out):
                        out.append({"name": name, "brief": brief})
            if len(out) >= 3:
                return out
            last_err = f"能用的点子只有 {len(out)} 个"
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("主角点子第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise SeriesError(f"主角点子没出好({last_err}),再点一次试试。")


async def suggest_plots(character: SeriesCharacter, used: list[str] | None = None) -> list[str]:
    """没灵感:按定妆形象出 3 个下一集剧情点子,避开已经用过的剧情。"""
    look = (character.look or "").strip()
    if not look:
        raise SeriesError("这个主角还没有定妆描述——先写好(或让 AI 代写)再要剧情点子。")
    used_lines = [str(u or "").strip() for u in (used or [])]
    used_lines = [u for u in used_lines if u]
    prompt = SERIES_PLOT_PROMPT.format(
        look=look[:1200],
        hints_block=_hints_block(character.style_hints),
        used_block="\n".join(f"- {u}" for u in used_lines) if used_lines else "(还没有已用剧情)",
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.SERIES_IDEA, timeout=300)
            plots = parse_llm_json(await adapter.ask(prompt)).get("plots")
            out: list[str] = []
            if isinstance(plots, list):
                for p in plots[:6]:
                    text = str(p or "").strip()[:PLOT_MAX]
                    if text and text not in out:
                        out.append(text)
            if len(out) >= 3:
                return out
            last_err = f"能用的点子只有 {len(out)} 个"
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("剧情点子第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise SeriesError(f"剧情点子没出好({last_err}),再点一次试试。")


async def draft_look(brief: str, direction: str, style_hints: str = "") -> str:
    """一句话概念 → 完整定妆描述(建角色时的草稿,不落库,用户确认后保存)。"""
    prompt = SERIES_LOOK_PROMPT.format(
        brief=brief.strip(),
        direction_directive=direction_directive(direction),
        hints_block=_hints_block(style_hints),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.SERIES_LOOK, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            look = str(data.get("look") or "").strip()[:LOOK_MAX]
            # 长短不拘(用户明确:定妆不一定非要几百字);空串=模型没写成
            if look:
                return look
            last_err = "AI 回了空的定妆描述"
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("定妆代写第 %d/%d 次未成:%s", attempt, _ATTEMPTS, last_err)
    raise SeriesError(f"定妆草稿没写好({last_err}),再点一次试试。")


async def generate_episode(
    db: Session, episode: SeriesEpisode, character: SeriesCharacter,
    progress=lambda s: None,
) -> dict:
    """定妆 + 剧情 → 一条成片提示词(长短不拘),写入 episode.output。"""
    look = (character.look or "").strip()
    if not look:
        raise SeriesError("这个角色还没有定妆描述——先在档案里写好(或让 AI 代写)再生成。")

    prompt = SERIES_EPISODE_PROMPT.format(
        duration_s=episode.duration_s,
        look=look,
        direction_directive=direction_directive(character.direction),
        hints_block=_hints_block(character.style_hints),
        plot=episode.plot.strip(),
    )
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        progress(f"AI 正在写第 {episode.id} 集的成片提示词…(第 {attempt} 次)")
        try:
            adapter = get_adapter_for(Task.SERIES_PROMPT, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            # 负面词走 merge_negative(negative, ""):base 为空仍会摘掉音频词
            # (音频词只进提示词正文,负面框是给画面用的——全站口径)
            output = norm_output(
                {**data, "negative": merge_negative(str(data.get("negative") or ""), "")},
                fallback_title=episode.plot,
            )
            # 篇幅自由(用户明确:一两百字也收);空 prompt_cn=模型没写成
            if output["prompt_cn"]:
                episode.output = output
                episode.status = "done"
                db.commit()
                return output
            last_err = "AI 回了空的成片提示词"
        except SeriesError:
            raise
        except Exception as exc:  # noqa: BLE001 — 重试一次,再失败才上屏
            last_err = str(exc)
        logger.warning("第 %d 集提示词生成第 %d/%d 次未成:%s",
                       episode.id, attempt, _ATTEMPTS, last_err)
    raise SeriesError(f"这集的提示词没写好({last_err}),再点一次生成试试。")
