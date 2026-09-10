# app/engines/drama/script.py
# -*- coding: utf-8 -*-
"""单集剧本:按集规划 + 源章节正文 + 角色卡声线,写出可拆分镜的台词稿。

对白演绎(dialogue)为主模式:角色台词驱动;口播解说(narration):旁白为主。
重写剧本会把状态拉回 scripted(旧分镜如仍在,前端提示已过期)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DramaCharacterCard, DramaEpisode, Project
from app.engines.common import ask_llm_json, parse_llm_json_checked
from app.engines.drama.common import (
    MODE_DESC,
    chapters_final_text,
    clip,
    episode_dict,
    episode_source_chapters,
    source_chapter_label,
)
from app.engines.drama.quality import (
    end_state_block,
    push_version,
    store_end_state,
    tail_lines_text,
    validate_drama_script,
)
from app.llm.router import Task, get_adapter_for
from app.prompts.drama import DRAMA_END_STATE_PROMPT, EPISODE_SCRIPT_PROMPT

# 源章节正文注入上限(字符):剧本只需要主体情节,超长正文截断防提示词爆炸。
# 数章并一集时这是「总预算」,按章平分;超预算的章保头尾去中段(见
# common.chapters_final_text——章尾是卡点素材的来源,不能砍)
_MAX_CHAPTER_CHARS = 9000
_MAX_LINES = 40
_ATTEMPTS = 2  # 输出崩坏(空壳/截断/说话人全空)时整发重试一次


class DramaScriptError(ValueError):
    """剧本生成的业务性错误(信息直接上屏)。"""


def _characters_block(db: Session, project_id: int) -> str:
    """角色速览:名字 + 声线 + 标志服饰(台词口感与辨识度用)。"""
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .order_by(DramaCharacterCard.id)
        .all()
    )
    if not cards:
        return ""
    lines = [
        f"  {c.name}|声线:{c.voice_desc or '未定'}|标志:{c.outfit_cn or '未定'}"
        for c in cards
    ]
    return "【角色速览(台词口感参考)】\n" + "\n".join(lines) + "\n"


def _prev_block(db: Session, project_id: int, ep_index: int) -> str:
    """上一集结尾卡点:保证集间衔接不断档。"""
    prev = (
        db.query(DramaEpisode.cliffhanger, DramaEpisode.title)
        .filter(
            DramaEpisode.project_id == project_id,
            DramaEpisode.ep_index == ep_index - 1,
        )
        .first()
    )
    if prev and prev.cliffhanger:
        return f"【上一集结尾卡点(开场要承接)】{prev.cliffhanger}\n"
    return ""


def _prev_state_block(db: Session, project_id: int, ep_index: int) -> str:
    """上一集集末状态契约(§5.2):时间/地点/在场/未了线索的硬约束块。

    与 `_prev_block` 各司其职:那个给的是「卡在哪」的一句话氛围,这个给的是
    「此刻是什么局面」的事实。此前漫剧线只有前者——时间和在场人物全靠模型
    自己从前文悟,悟错无兜底(小说侧 handoff / 剧本线都有契约,漫剧是缺口)。
    """
    prev = (
        db.query(DramaEpisode)
        .filter(
            DramaEpisode.project_id == project_id,
            DramaEpisode.ep_index == ep_index - 1,
        )
        .first()
    )
    return end_state_block(prev)


def _focus_block(episode: DramaEpisode) -> str:
    """本集重点(作者改编意图):给了就是最高优先级的再创作指令。"""
    focus = (getattr(episode, "focus", "") or "").strip()
    if not focus:
        return ""
    return f"【本集重点(作者指定,最高优先级遵循)】{focus[:200]}\n"


async def write_episode_script(
    db: Session, project: Project, episode: DramaEpisode, progress=lambda s: None
) -> dict:
    # 一集可能由数章合并而来:逐章取正文,只喂主章会把并进来的章丢掉
    wanted = episode_source_chapters(episode)
    if not wanted:
        raise DramaScriptError("这一集没有源章号,先重新「切集」。")
    body, got = chapters_final_text(db, project.id, wanted, _MAX_CHAPTER_CHARS)
    if not body:
        raise DramaScriptError(
            f"{source_chapter_label(episode)}没有正文,先在写作区生成并定稿这些章。"
        )
    missing = [n for n in wanted if n not in got]
    if missing:
        progress(
            "第 " + "、".join(str(n) for n in missing) + " 章还没有正文,本集只按已定稿的章写…"
        )
    used_label = (
        source_chapter_label(episode)
        if not missing
        else "第 " + "、".join(str(n) for n in got) + " 章"
    )

    progress(f"AI 正在写第 {episode.ep_index} 集剧本({episode.mode})…")
    adapter = get_adapter_for(Task.DRAMA_SCRIPT, timeout=300)
    prompt = EPISODE_SCRIPT_PROMPT.format(
        title=project.title,
        ep_index=episode.ep_index,
        ep_title=episode.title,
        mode_desc=MODE_DESC.get(episode.mode, MODE_DESC["dialogue"]),
        duration_target_s=episode.duration_target_s,
        hook=episode.hook or "(规划未给,自行设计强钩子开场)",
        recap=episode.recap,
        cliffhanger=episode.cliffhanger or "(规划未给,自行设计卡点结尾)",
        prev_block=_prev_block(db, project.id, episode.ep_index),
        prev_state_block=_prev_state_block(db, project.id, episode.ep_index),
        focus_block=_focus_block(episode),
        characters_block=_characters_block(db, project.id),
        source_label=used_label,
        chapter_text=body,
    )

    # 格式门禁 + 整发重试:只挡「没写成」(空壳/JSON 崩/台词被截断/说话人全空),
    # 不评判文笔。中转网关截断尾巴是常态不是意外,一次不成重试一次再报错。
    lines_out: list[dict] = []
    last_err = "模型返回空内容"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            raw = await adapter.ask(prompt)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            progress(f"第 {episode.ep_index} 集剧本第 {attempt}/{_ATTEMPTS} 次调用失败,重试中…")
            continue
        data, err = parse_llm_json_checked(raw)
        ok, why = validate_drama_script(data)
        if not ok:
            last_err = why or err or "输出不可用"
            progress(f"第 {episode.ep_index} 集剧本第 {attempt}/{_ATTEMPTS} 次输出不可用({last_err}),重试中…")
            continue
        lines_out = _clean_lines(data.get("lines"))
        if not lines_out:
            last_err = "台词清洗后为空"
            continue
        last_err = ""
        break
    if not lines_out:
        raise DramaScriptError(f"剧本生成失败:{last_err}")

    # 覆盖前存一版(手改/重写可回溯)。**必须在写新剧本之前**——顺序反了就
    # 把旧版存成了新版内容,快照彻底失效。
    push_version(episode, source="generated")
    script = dict(episode.script) if isinstance(episode.script, dict) else {}
    script["mode"] = episode.mode
    script["synopsis"] = clip(data.get("synopsis"), 300)
    script["lines"] = lines_out
    episode.script = script
    episode.status = "scripted"

    # 集末契约:给下一集用的衔接锚。**必须在 lines 落进 episode.script 之后**——
    # 提取读的是本集结尾台词(tail_lines_text),排在前头只会读到空剧本。
    # 失败只降级(标 failed),不推翻已经写好的正本。
    await _store_end_state(episode, adapter)

    db.commit()
    return episode_dict(episode)


def _clean_lines(raw_lines) -> list[dict]:
    """原始 lines → 干净台词行(丢空 text、截长、封顶 _MAX_LINES)。

    与门禁分离:门禁判「能不能用」,这里负责「怎么落库」——判据可被单测
    直接喂数据验证,不必经 LLM。
    """
    out: list[dict] = []
    for item in (raw_lines or []):
        if not isinstance(item, dict):
            continue
        text_line = clip(item.get("text"), 300)
        if not text_line:
            continue
        out.append(
            {
                "speaker": clip(item.get("speaker"), 60) or "旁白",
                "text": text_line,
                "action": clip(item.get("action"), 120),
            }
        )
        if len(out) >= _MAX_LINES:
            break
    return out


async def _store_end_state(episode: DramaEpisode, adapter) -> None:
    """提取并落集末契约;失败记 failed(界面可提示重提),不影响本集剧本。"""
    n = episode.ep_index
    tail = tail_lines_text(episode)
    if not tail:
        store_end_state(episode, None, "本集没有可提取的台词")
        return
    prompt = DRAMA_END_STATE_PROMPT.format(n=n, tail=tail)
    # 契约是衔接增强,不值得为它烧满重试预算:整发重发 1 次 + 续写抢救 1 次封顶。
    try:
        data, err = await ask_llm_json(
            adapter, prompt, label=f"第 {n} 集集末契约",
            attempts=1, continue_attempts=1,
        )
    except Exception as exc:  # noqa: BLE001 — 契约是衔接增强,不该拖垮正文
        err, data = str(exc), {}
    if err or not data:
        store_end_state(episode, None, err or "模型返回空内容")
        return
    store_end_state(episode, data)
