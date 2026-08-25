# app/engines/clips/batch.py
# -*- coding: utf-8 -*-
"""情绪短片批产:两段式产三个不同切入的本子(通用版/小说衍生版同一入口)。

为什么两段:一次要吐「风格卡 + 3 个本子 × 最多 7 格 × 三轨提示词」是全站最大的
单次输出,被 max_tokens 砍断就三个本子一起白跑。改成
①一发定风格卡 + 三条切入(小输出),②三发并行各自展开分镜(各自重试)。
一发失败还剩两个可用本子——`_MIN_CANDIDATES` 是「算成功」的下限。
风格卡在①定死后原样传进②:三个本子共用一套画风是产品红线(三选一后提示词口径一致)。

确定性部分:归一化(镜头数/时长收敛)、切段分组(复用 common.group_chunks)、
画风锚兜底、小说衍生版的金句溯源校验(quote_source 必须能在提供的正文节选里找到,
找不到进 cautions——聊天窗口给不了的纪律)。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, DramaCharacterCard, MoodClip, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.clips.common import group_chunks, shot_hint, steering_block, theme_label
from app.engines.media.anchors import ensure_style_anchors, merge_negative
from app.engines.media.directions import direction_directive
from app.engines.media.text import coerce_int
from app.llm.router import Task, get_adapter_for
from app.prompts.clips import (
    CLIPS_CLICHE_BLACKLIST,
    CLIPS_EXPAND_PROMPT,
    CLIPS_GENERIC_CONTEXT,
    CLIPS_GROUNDING_RULE,
    CLIPS_NOVEL_CONTEXT,
    CLIPS_STRUCTURE_RULES,
    CLIPS_TAKES_PROMPT,
    expand_feedback_block,
    takes_feedback_block,
)

logger = logging.getLogger(__name__)

_MAX_SHOTS = 7
_MAX_LINES = 10
# 小说衍生:节选最多取几章、每章截多长(字符)
_EXCERPT_CHAPTERS = 3
_EXCERPT_CHARS = 1200
# 产品口径:三选一。少于 2 个没得选,算失败
_TAKES = 3
_MIN_CANDIDATES = 2
# 单条切入展开失败后再试一次(整发重来:LLM 上一次可能吐了半截 JSON)
_EXPAND_ATTEMPTS = 2
# 通用入口的 quote_hint 占位文案:模型常把它逐字回填进 quote_source,
# 原样入库会跟着导出手卡印出「金句原句:(通用入口留空)」,归一化时摘掉。
_GENERIC_QUOTE_PLACEHOLDER = "(通用入口留空)"


class ClipBatchError(ValueError):
    """批产的业务性错误(信息直接上屏)。"""


# =============== 小说素材拼装 ===============

def _novel_context(db: Session, project: Project) -> tuple[str, str]:
    """返回 (正文节选块, 角色锚块)。节选取最新定稿章;角色锚优先用漫剧角色卡。"""
    rows = (
        db.query(Chapter.chapter_number, Chapter.final_content)
        .filter(Chapter.project_id == project.id, Chapter.status == "approved")
        .order_by(Chapter.chapter_number.desc())
        .limit(_EXCERPT_CHAPTERS)
        .all()
    )
    if not rows:
        raise ClipBatchError("这本书还没有已定稿章节——先写几章再来出投流短视频。")
    parts = []
    for n, content in sorted(rows):
        text = (content or "").strip()
        if text:
            parts.append(f"【第{n}章 节选】\n{text[:_EXCERPT_CHARS]}")
    excerpts = "\n\n".join(parts)

    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .order_by(DramaCharacterCard.id)
        .limit(4)
        .all()
    )
    char_lines = [
        f"【{c.name}】{c.appearance_cn}" + (f"\n  EN: {c.appearance_en}" if c.appearance_en else "")
        for c in cards
        if c.appearance_cn
    ]
    characters = "\n".join(char_lines) if char_lines else "(无角色卡,按节选中人物自行合理设计并保持一致)"
    return excerpts, characters


def _concept_line(project: Project) -> str:
    c = project.concept if isinstance(project.concept, dict) else {}
    logline = str(c.get("logline") or "").strip()
    return f"【一句话故事】{logline}\n" if logline else ""


# =============== 归一化 ===============

def _norm_shots(raw, style: dict, max_seq_cap: int) -> list[dict]:
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        prompt_cn = str(item.get("prompt_cn") or "").strip()[:800]
        prompt_en = str(item.get("prompt_en") or "").strip()[:600]
        negative = str(item.get("negative") or "").strip()[:400]
        # LLM 漏写画面提示词时用分镜自身的景别/运镜/动作拼一条确定性兜底——
        # 空着交给画风锚,会产出一条「只有风格锚、没有画面内容」的提示词
        if not prompt_cn:
            prompt_cn = (
                f"{str(item.get('shot_type') or '').strip()}"
                f"/{str(item.get('camera') or '').strip()}:{action}"
            )
        # 画风锚/负面词兜底(与漫剧、宣传片同一口径,见 media.anchors)
        prompt_cn, prompt_en = ensure_style_anchors(
            prompt_cn, prompt_en, style.get("style_cn") or "", style.get("style_en") or ""
        )
        negative = merge_negative(negative, style.get("negative") or "")
        out.append(
            {
                "seq": len(out) + 1,
                "scene_name": str(item.get("scene_name") or "").strip()[:200],
                "characters": [
                    str(c).strip() for c in (item.get("characters") or []) if str(c or "").strip()
                ][:2],
                "action_desc": action[:200],
                "shot_type": str(item.get("shot_type") or "").strip()[:20],
                "camera": str(item.get("camera") or "").strip()[:20],
                "dialogue": str(item.get("dialogue") or "").strip()[:200],
                "duration_s": coerce_int(item.get("duration_s"), 3, lo=1, hi=8),
                "prompt_cn": prompt_cn,
                "prompt_en": prompt_en,
                "negative": negative,
            }
        )
        if len(out) >= max_seq_cap:
            break
    return out


def _norm_style(data: dict) -> dict:
    """风格卡归一化:①的产出,三个本子共用。"""
    return {
        "style_name": str(data.get("style_name") or "").strip()[:60],
        "style_cn": str(data.get("style_cn") or "").strip()[:400],
        "style_en": str(data.get("style_en") or "").strip()[:400],
        "negative": str(data.get("negative") or "").strip()[:300],
    }


def _norm_takes(data: dict) -> list[dict]:
    """三条切入归一化。take 缺失就补序号名,logline 空的丢掉(没内容没法展开)。"""
    takes = []
    for item in (data.get("takes") or []):
        if not isinstance(item, dict):
            continue
        logline = str(item.get("logline") or "").strip()[:200]
        if not logline:
            continue
        quote_source = str(item.get("quote_source") or "").strip()[:300]
        if quote_source == _GENERIC_QUOTE_PLACEHOLDER:
            quote_source = ""
        takes.append(
            {
                "take": str(item.get("take") or "").strip()[:60] or f"切入{len(takes) + 1}",
                "logline": logline,
                "emotion_curve": str(item.get("emotion_curve") or "").strip()[:120],
                "punchline": str(item.get("punchline") or "").strip()[:60],
                "hook_text": str(item.get("hook_text") or "").strip()[:60],
                "quote_source": quote_source,
            }
        )
        if len(takes) >= _TAKES:
            break
    return takes


def _norm_lines(raw) -> list[dict]:
    lines = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:120]
        if text:
            lines.append(
                {
                    "speaker": str(item.get("speaker") or "旁白").strip()[:40],
                    "text": text,
                    "action": str(item.get("action") or "").strip()[:100],
                }
            )
        if len(lines) >= _MAX_LINES:
            break
    return lines


def _quote_grounded(quote: str, excerpts: str) -> bool:
    """金句溯源:原句(去空白)需能在节选(去空白)里找到。"""
    q = "".join(str(quote or "").split())
    if not q:
        return False
    body = "".join(excerpts.split())
    return q in body


def _build_candidate(
    take: dict, expanded: dict, style: dict, duration_s: int, excerpts: str = ""
) -> dict | None:
    """①的一条切入 + ②的展开结果 → 一个完整候选本子。展开没给出分镜就返回 None。"""
    max_shots = 5 if duration_s <= 15 else _MAX_SHOTS
    shots = _norm_shots(expanded.get("shots"), style, max_shots)
    if not shots:
        return None

    cautions = []
    quote_source = take.get("quote_source") or ""
    if excerpts:
        # 小说衍生才校验:金句必须能在正文节选里找到,编造的引用要显式警示
        if not quote_source:
            cautions.append("未给出金句原句,请人工核对是否出自正文")
        elif not _quote_grounded(quote_source, excerpts):
            cautions.append(f"金句原句未在正文节选中找到,请核实:{quote_source[:60]}")
    total = sum(s["duration_s"] for s in shots)
    if abs(total - duration_s) > max(6, duration_s // 3):
        cautions.append(f"分镜总时长 {total}s 与目标 {duration_s}s 偏差较大,拼接时注意")

    return {
        "take": take["take"],
        "logline": take["logline"],
        "emotion_curve": take.get("emotion_curve", ""),
        "lines": _norm_lines(expanded.get("lines")),
        "shots": shots,
        "punchline": take.get("punchline", ""),
        "chunks": group_chunks(shots, 15),
        "hook_text": take.get("hook_text", ""),
        "quote_source": quote_source,
        "cautions": cautions,
    }


# =============== 批产入口 ===============

def _build_context(db: Session, clip: MoodClip) -> tuple[str, str, str]:
    """按入口拼背景块。返回 (背景块, 正文节选, 金句红线块)。

    节选只在小说衍生入口非空——它同时是「要不要做金句溯源校验」的开关。
    steering_block(台词风格/节奏/情绪浓度/氛围关键词)对两段提示词同时生效。
    """
    inspiration_block = (
        f"【用户灵感种子(三个本子都要围着它生长,不可偏离)】{clip.inspiration.strip()}\n"
        if clip.inspiration.strip()
        else ""
    )
    steering = steering_block(clip)
    directive = direction_directive(clip.direction or "live")

    if clip.source_project_id:
        project = db.get(Project, clip.source_project_id)
        if project is None:
            raise ClipBatchError("源项目不存在(可能已删除)。")
        excerpts, characters = _novel_context(db, project)
        context = CLIPS_NOVEL_CONTEXT.format(
            title=project.title,
            genre=project.genre or "不限",
            topic=(project.topic or "").strip() or "(未定)",
            concept_block=_concept_line(project),
            excerpts_block=excerpts,
            characters_block=characters,
            duration_s=clip.duration_s,
            direction_directive=directive,
            steering_block=steering,
            inspiration_block=inspiration_block,
        )
        return context, excerpts, CLIPS_GROUNDING_RULE

    if not (clip.theme or clip.custom_theme.strip()):
        raise ClipBatchError("先选一个情绪主题(或填自定义主题)。")
    context = CLIPS_GENERIC_CONTEXT.format(
        theme_label=theme_label(clip),
        duration_s=clip.duration_s,
        direction_directive=directive,
        steering_block=steering,
        inspiration_block=inspiration_block,
    )
    return context, "", ""


async def _expand_one(
    take: dict, style: dict, duration_s: int, context: str, grounding: str, excerpts: str,
    feedback: str = "",
) -> dict | None:
    """把一条切入展开成完整本子。整发重试 `_EXPAND_ATTEMPTS` 次,仍不成返回 None。

    **每次都自己 `get_adapter_for` 拿新适配器**:`complete_text_with_budget` 会
    临时改写 `adapter.max_tokens` 再在 finally 里还原,三发共用一个实例会互相
    篡改预算(A 翻倍时 B 把翻倍后的值当原值存下来再还原回去)。

    只收普通值、不收 ORM 行:并发协程里碰 ORM 属性,一旦属性已过期就会三条一起
    触发懒加载查询打同一个 session——`database is locked` 的老根因就是这么来的。

    feedback:单条重拍时的用户意见,注入提示词(切入与画风不变,只重展开)。
    """
    prompt = CLIPS_EXPAND_PROMPT.format(
        context_block=context,
        structure_rules=CLIPS_STRUCTURE_RULES,
        cliche_blacklist=CLIPS_CLICHE_BLACKLIST,
        style_cn=style["style_cn"] or "(未给出,请自行统一并保持三条一致)",
        style_en=style["style_en"],
        negative=style["negative"],
        take=take["take"],
        logline=take["logline"],
        emotion_curve=take.get("emotion_curve") or "(未给出)",
        punchline=take.get("punchline") or "(未给出,请自拟一句戳心收尾)",
        duration_s=duration_s,
        shot_hint=shot_hint(duration_s),
        feedback_block=expand_feedback_block(feedback),
        grounding_rule=grounding,
    )
    for attempt in range(1, _EXPAND_ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            cand = _build_candidate(take, data, style, duration_s, excerpts=excerpts)
            if cand is not None:
                return cand
            logger.warning("本子「%s」展开没拿到分镜(第 %d/%d 次)", take["take"], attempt, _EXPAND_ATTEMPTS)
        except Exception as exc:  # noqa: BLE001 — 一条失败不许拖垮另外两条
            logger.warning(
                "本子「%s」展开失败(第 %d/%d 次): %s", take["take"], attempt, _EXPAND_ATTEMPTS, exc
            )
    return None


async def generate_batch(
    db: Session, clip: MoodClip, progress=lambda s: None, feedback: str = ""
) -> dict:
    """两段式产三个本子:①定风格 + 三条切入,②三发并行展开。

    feedback:换一批时的用户意见——连同上一批三条切入的摘要进①的提示词,
    这批要避开旧方向、落实意见;首跑传空。
    """
    context, excerpts, grounding = _build_context(db, clip)

    # ---- ① 风格卡 + 三条切入(小输出,一发)----
    progress("AI 正在定画风、想 3 个不同切入…" if not feedback.strip()
             else "AI 正在按你的意见想 3 个新切入…")
    adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
    head = parse_llm_json(
        await adapter.ask(
            CLIPS_TAKES_PROMPT.format(
                context_block=context,
                structure_rules=CLIPS_STRUCTURE_RULES,
                cliche_blacklist=CLIPS_CLICHE_BLACKLIST,
                feedback_block=takes_feedback_block(clip.candidates or [], feedback),
                quote_hint=(
                    "本子核心金句在正文里的原句(逐字摘自节选;轻度改写时原句也照抄在此)"
                    if excerpts
                    else _GENERIC_QUOTE_PLACEHOLDER
                ),
                grounding_rule=grounding,
            )
        )
    )
    style = _norm_style(head)
    takes = _norm_takes(head)
    if len(takes) < _MIN_CANDIDATES:
        raise ClipBatchError("AI 没给出足够的切入方向,请重试。")

    # ---- ② 三发并行展开(各自重试;一发失败不影响其他)----
    duration_s = clip.duration_s  # 先取出来:并发协程里不碰 ORM 行(见 _expand_one)
    done = 0

    async def run(take: dict) -> dict | None:
        nonlocal done
        cand = await _expand_one(take, style, duration_s, context, grounding, excerpts)
        done += 1
        # 完成计数式进度:并发下「第几个」没有意义,报「已出几个」才对得上用户看到的卡片数
        progress(f"已出 {done}/{len(takes)} 个本子…")
        return cand

    results = await asyncio.gather(*(run(t) for t in takes))
    candidates = [c for c in results if c]
    if len(candidates) < _MIN_CANDIDATES:
        raise ClipBatchError("候选本子过少,请重试。")
    if len(candidates) < len(takes):
        logger.warning("%d/%d 条切入展开失败,按现有候选交付", len(takes) - len(candidates), len(takes))

    clip.style_name = style["style_name"]
    clip.style_cn = style["style_cn"]
    clip.style_en = style["style_en"]
    clip.negative = style["negative"]
    clip.candidates = candidates
    clip.chosen = -1
    clip.clip = {}
    clip.status = "generated"
    db.commit()
    return {"candidates": candidates, "style_name": clip.style_name}


async def reexpand_batch(
    db: Session, clip: MoodClip, index: int, feedback: str, progress=lambda s: None
) -> dict:
    """单条重拍:保持风格卡与该条切入不变,只重新展开分镜(可带用户意见)。

    换一批(整批重来)与单条重拍的分工:切入方向不对 → 换一批带意见;
    方向对但执行差(分镜平/台词多/提示词弱)→ 重拍这条。
    重拍的是已选定的那条时,chosen 重置为未选——内容变了必须重新确认,不静默替换手卡。
    """
    candidates = list(clip.candidates or [])
    if not (0 <= index < len(candidates)):
        raise ClipBatchError(f"候选序号无效:{index}(共 {len(candidates)} 个)")
    old = candidates[index]
    take = {
        "take": old.get("take") or f"切入{index + 1}",
        "logline": old.get("logline") or "",
        "emotion_curve": old.get("emotion_curve") or "",
        "punchline": old.get("punchline") or "",
        "hook_text": old.get("hook_text") or "",
        "quote_source": old.get("quote_source") or "",
    }
    style = {
        "style_cn": clip.style_cn or "",
        "style_en": clip.style_en or "",
        "negative": clip.negative or "",
    }
    context, excerpts, grounding = _build_context(db, clip)
    progress(f"AI 正在重拍「{take['take']}」…")
    cand = await _expand_one(
        take, style, clip.duration_s, context, grounding, excerpts, feedback=feedback
    )
    if cand is None:
        raise ClipBatchError(
            f"「{take['take']}」重拍失败(分镜没出来),原候选保持不变,请重试。"
        )
    candidates[index] = cand
    clip.candidates = candidates
    if clip.chosen == index:
        # 重拍的是已选定条:旧手卡对应的内容已被替换,必须重新三选一
        clip.chosen = -1
        clip.clip = {}
        clip.status = "generated"
    db.commit()
    return {"candidates": candidates}


def pick_clip(db: Session, clip: MoodClip, index: int) -> dict:
    """选定第 index 个候选(0 起)为最终本子。"""
    candidates = clip.candidates or []
    if not (0 <= index < len(candidates)):
        raise ClipBatchError(f"候选序号无效:{index}(共 {len(candidates)} 个)")
    clip.chosen = index
    clip.clip = candidates[index]
    clip.status = "picked"
    db.commit()
    from app.engines.clips.common import clip_dict

    return clip_dict(clip)
