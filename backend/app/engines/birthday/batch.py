# app/engines/birthday/batch.py
# -*- coding: utf-8 -*-
"""生日祝福批产:两段式产三个不同切入的本子(寿星定制,资料驱动)。

骨架与情绪短片同构(①风格卡+三条切入小输出,②三发并行展开防截断、单发重试),
差异全在定制侧:
- 背景块是寿星资料(称呼/关系/里程碑/回忆点/送出人),不是抽象命题;
- **回忆点核对**(生日版的灵魂):memories 非空时,把候选全部分镜/台词拼成语料,
  逐条回忆点做二元组重合核对——对不上的进 cautions 提示用户核实。
  定制片最怕的不是平庸,是张冠李戴的「假回忆」;宁可误报让用户看一眼,
  不可漏报让寿星看到一段没有发生过的事。

确定性部分:归一化(镜头数/时长收敛)、切段分组(复用 common.group_chunks)、
画风锚兜底(media.anchors),与三线同口径。
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.orm import Session

from app.db.models import BirthdayWish
from app.engines.consistency.extractor import parse_llm_json
from app.engines.birthday.common import (
    group_chunks,
    pack_directive,
    relationship_label,
    shot_hint,
    tone_label,
)
from app.engines.media.anchors import ensure_style_anchors, merge_negative
from app.engines.media.directions import direction_directive
from app.engines.media.text import coerce_int, split_character_desc
from app.llm.router import Task, get_adapter_for
from app.prompts.birthday import (
    BIRTHDAY_CLICHE_BLACKLIST,
    BIRTHDAY_CONTEXT,
    BIRTHDAY_EXPAND_PROMPT,
    BIRTHDAY_GROUNDING_RULE,
    BIRTHDAY_PROMPT_DETAILS,
    BIRTHDAY_STRUCTURE_RULES,
    BIRTHDAY_TAKES_PROMPT,
    expand_feedback_block,
    takes_feedback_block,
)

logger = logging.getLogger(__name__)

_MAX_SHOTS = 12   # 60s 上限格数(30s 在 _build_candidate 里收到 7)
_MAX_LINES = 16   # 60s 的台词上限(4 字/秒贴着走也用不了这么多)
# 产品口径:三选一。少于 2 个没得选,算失败
_TAKES = 3
_MIN_CANDIDATES = 2
# 单条切入展开失败后再试一次(整发重来:LLM 上一次可能吐了半截 JSON)
_EXPAND_ATTEMPTS = 2


class BirthdayBatchError(ValueError):
    """批产的业务性错误(信息直接上屏)。"""


# =============== 归一化(口径与 clips 同源,各自维护:两线的上限与字段随产品分叉) ===============

def _norm_character_cards(shots: list[dict], style_note: str = "") -> list[dict]:
    """从分镜聚合角色定妆卡:按角色名切分外貌描段、跨镜头合并,并入统一画风。

    从 shots 聚合而不是信展开的顶层字段:编辑保存(PUT /wish)只回传
    {"lines","shots"} 再走 `_build_candidate`,顶层字段会在那里丢。
    角色切分走 media.text.split_character_desc(与 clips 同源):模型常把多角色
    混写进一段,按镜头笼统塞给每个角色,卡就互相串了。
    """
    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for s in shots:
        names = [str(x).strip() for x in (s.get("characters") or []) if str(x or "").strip()]
        desc = str(s.get("character_desc") or "").strip()
        if not desc:
            continue
        spans = split_character_desc(desc, names)
        for name in names:
            if name in ("", "旁白"):
                continue
            span = (spans.get(name) or "").strip()
            if not span:
                continue
            if name not in merged:
                merged[name] = []
                order.append(name)
            if span not in merged[name]:
                merged[name].append(span)
    cards = []
    for name in order:
        desc = "\n".join(merged[name])[:1200]
        if style_note:
            desc = f"{desc}\n{style_note}" if desc else style_note
        cards.append({"name": name[:40], "desc": desc})
    return cards


def _norm_shots(raw, style: dict, max_seq_cap: int) -> list[dict]:
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        prompt_cn = str(item.get("prompt_cn") or "").strip()[:1200]
        prompt_en = str(item.get("prompt_en") or "").strip()[:800]
        negative = str(item.get("negative") or "").strip()[:500]
        character_desc = str(item.get("character_desc") or "").strip()[:600]
        # LLM 漏写画面提示词时用分镜自身的景别/运镜/动作拼一条确定性兜底——
        # 空着交给画风锚,会产出一条「只有风格锚、没有画面内容」的提示词
        if not prompt_cn:
            prompt_cn = (
                f"{str(item.get('shot_type') or '').strip()}"
                f"/{str(item.get('camera') or '').strip()}:{action}"
            )
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
                ][:3],
                "character_desc": character_desc,
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
        takes.append(
            {
                "take": str(item.get("take") or "").strip()[:60] or f"切入{len(takes) + 1}",
                "logline": logline,
                "emotion_curve": str(item.get("emotion_curve") or "").strip()[:120],
                "punchline": str(item.get("punchline") or "").strip()[:60],
                "hook_text": str(item.get("hook_text") or "").strip()[:60],
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


# =============== 回忆点核对(生日版灵魂) ===============

# 二元组过滤用的高频虚词:两边都是虚词的二元组(的一/了我/是在)不作为「落实」证据
_STOP_CHARS = set("的一了是在我有和就不人也个上到说要会着看这那你他她它们么呢吧啊呀哦嘛与及或被把让给从对")


def _clean_for_ngrams(text: str) -> str:
    """去空白与标点,只留汉字/字母/数字(标点写法不同会干扰重合判断)。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or ""))


def _distinct_bigrams(text: str) -> set[str]:
    """提取「有信息量」的字符二元组(两边全是虚词的丢弃)。"""
    t = _clean_for_ngrams(text)
    grams: set[str] = set()
    for i in range(len(t) - 1):
        a, b = t[i], t[i + 1]
        if a in _STOP_CHARS and b in _STOP_CHARS:
            continue
        grams.add(a + b)
    return grams


def _memories_grounded(memories: list[str], shots: list[dict], lines: list[dict]) -> list[str]:
    """返回没被分镜落实的回忆点(进 cautions)。

    判据:回忆点的二元组与「全部分镜场景/画面/台词/提示词 + 台词表」的二元组
    重合 ≥2 个算落实。LLM 会换说法(天台→楼顶),完全对不上时才报——报出来的
    那条要么是 AI 编的回忆、要么是表述差太远,都值得用户看一眼。
    """
    corpus: set[str] = set()
    for s in shots:
        corpus |= _distinct_bigrams(
            f"{s.get('scene_name') or ''}{s.get('action_desc') or ''}"
            f"{s.get('dialogue') or ''}{s.get('prompt_cn') or ''}"
        )
    for l in lines:
        corpus |= _distinct_bigrams(f"{l.get('speaker') or ''}{l.get('text') or ''}")
    missing = []
    for m in memories:
        grams = _distinct_bigrams(m)
        # 全被虚词过滤的回忆点(极短/纯语气词)没有可核对的证据,不误报
        if grams and len(grams & corpus) < 2:
            missing.append(m)
    return missing


def _build_candidate(
    take: dict, expanded: dict, style: dict, duration_s: int, memories: list[str]
) -> dict | None:
    """①的一条切入 + ②的展开结果 → 一个完整候选本子。展开没给出分镜就返回 None。"""
    max_shots = 7 if duration_s <= 30 else _MAX_SHOTS
    shots = _norm_shots(expanded.get("shots"), style, max_shots)
    if not shots:
        return None
    lines = _norm_lines(expanded.get("lines"))

    cautions = []
    for m in _memories_grounded(memories, shots, lines):
        cautions.append(f"回忆点「{m[:40]}」没在分镜里找到对应画面,请核实是否被落实")
    total = sum(s["duration_s"] for s in shots)
    if abs(total - duration_s) > max(8, duration_s // 3):
        cautions.append(f"分镜总时长 {total}s 与目标 {duration_s}s 偏差较大,拼接时注意")

    # 定妆卡并入画风锚:定妆照与正片同受一条风格卡约束(与 clips 同款),
    # 文生图时一并贴上,出来的参考图才和正片一个气质
    sn = (style.get("style_name") or "").strip()
    sc = (style.get("style_cn") or "").strip()
    style_note = f"【定妆照画风】{sn}:{sc}" if (sn or sc) else ""

    return {
        "take": take["take"],
        "logline": take["logline"],
        "emotion_curve": take.get("emotion_curve", ""),
        "lines": lines,
        "shots": shots,
        "character_cards": _norm_character_cards(shots, style_note=style_note),
        "punchline": take.get("punchline", ""),
        "chunks": group_chunks(shots, 15),
        "hook_text": take.get("hook_text", ""),
        "cautions": cautions,
    }


# =============== 批产入口 ===============

def _style_directive(wish: BirthdayWish) -> str:
    """画风硬约束块:风格包优先(自带画风锚+世界观+主角植入),无包走通用方向。

    风格包与通用方向是互斥的两态——包的 directive 已把画面质感锁死,再叠一条
    「真人写实」一类的通用方向就是两套硬约束打架,模型两头摇摆。
    """
    pack = (getattr(wish, "pack", "") or "").strip()
    directive = pack_directive(pack)
    if directive:
        return f"【风格包(硬约束:画风与世界观以此为准,主角植入必须执行)】{directive}"
    return f"【画风方向(硬约束)】{direction_directive(wish.direction or 'live')}"


def _build_context(wish: BirthdayWish) -> str:
    """寿星资料 → 背景块。定制感的全部来源,逐项注入;回忆点是选材红线。"""
    memories = [str(m).strip() for m in (wish.memories or []) if str(m or "").strip()]
    memories_block = "\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1)) or "(未提供)"
    milestone_block = f"【里程碑】{wish.milestone.strip()}\n" if wish.milestone.strip() else ""
    hints_block = (
        f"【氛围关键词(必须自然融入画风卡与各格提示词的氛围)】{wish.style_hints.strip()}\n"
        if (wish.style_hints or "").strip()
        else ""
    )
    return BIRTHDAY_CONTEXT.format(
        tone_label=tone_label(wish),
        honoree_name=wish.honoree_name.strip() or "(未填)",
        relationship_label=relationship_label(wish.relationship),
        milestone_block=milestone_block,
        sender_desc=wish.sender_desc.strip() or "送出人(未说明,按单人视角)",
        memories_block=memories_block,
        duration_s=wish.duration_s,
        style_directive=_style_directive(wish),
        hints_block=hints_block,
    )


async def _expand_one(
    take: dict, style: dict, duration_s: int, context: str, memories: list[str],
    feedback: str = "",
) -> dict | None:
    """把一条切入展开成完整本子。整发重试 `_EXPAND_ATTEMPTS` 次,仍不成返回 None。

    **每次都自己 `get_adapter_for` 拿新适配器**:并发三发共用一个实例会互相篡改
    max_tokens 预算(complete_text_with_budget 临时改写再还原,时序一错就永久翻倍)。
    只收普通值、不收 ORM 行:并发协程里碰 ORM 属性会触发懒加载打同一个 session。
    """
    prompt = BIRTHDAY_EXPAND_PROMPT.format(
        context_block=context,
        structure_rules=BIRTHDAY_STRUCTURE_RULES,
        cliche_blacklist=BIRTHDAY_CLICHE_BLACKLIST,
        prompt_details=BIRTHDAY_PROMPT_DETAILS,
        style_cn=style["style_cn"] or "(未给出,请自行统一并保持三条一致)",
        style_en=style["style_en"],
        negative=style["negative"],
        take=take["take"],
        logline=take["logline"],
        emotion_curve=take.get("emotion_curve") or "(未给出)",
        punchline=take.get("punchline") or "(未给出,请自拟一句专属祝福收尾)",
        duration_s=duration_s,
        shot_hint=shot_hint(duration_s),
        feedback_block=expand_feedback_block(feedback),
        grounding_rule=BIRTHDAY_GROUNDING_RULE,
    )
    for attempt in range(1, _EXPAND_ATTEMPTS + 1):
        try:
            adapter = get_adapter_for(Task.BIRTHDAY_BATCH, timeout=300)
            data = parse_llm_json(await adapter.ask(prompt))
            cand = _build_candidate(take, data, style, duration_s, memories=memories)
            if cand is not None:
                return cand
            logger.warning("本子「%s」展开没拿到分镜(第 %d/%d 次)", take["take"], attempt, _EXPAND_ATTEMPTS)
        except Exception as exc:  # noqa: BLE001 — 一条失败不许拖垮另外两条
            logger.warning(
                "本子「%s」展开失败(第 %d/%d 次): %s", take["take"], attempt, _EXPAND_ATTEMPTS, exc
            )
    return None


async def generate_batch(
    db: Session, wish: BirthdayWish, progress=lambda s: None, feedback: str = ""
) -> dict:
    """两段式产三个本子:①定风格 + 三条切入,②三发并行展开。

    feedback:换一批时的用户意见——连同上一批三条切入的摘要进①的提示词,
    这批要避开旧方向、落实意见;首跑传空。
    """
    context = _build_context(wish)
    memories = [str(m).strip() for m in (wish.memories or []) if str(m or "").strip()]

    # ---- ① 风格卡 + 三条切入(小输出,一发)----
    progress("AI 正在定画风、想 3 个不同切入…" if not feedback.strip()
             else "AI 正在按你的意见想 3 个新切入…")
    adapter = get_adapter_for(Task.BIRTHDAY_BATCH, timeout=300)
    head = parse_llm_json(
        await adapter.ask(
            BIRTHDAY_TAKES_PROMPT.format(
                context_block=context,
                structure_rules=BIRTHDAY_STRUCTURE_RULES,
                cliche_blacklist=BIRTHDAY_CLICHE_BLACKLIST,
                feedback_block=takes_feedback_block(wish.candidates or [], feedback),
                grounding_rule=BIRTHDAY_GROUNDING_RULE,
            )
        )
    )
    style = _norm_style(head)
    takes = _norm_takes(head)
    if len(takes) < _MIN_CANDIDATES:
        raise BirthdayBatchError("AI 没给出足够的切入方向,请重试。")

    # ---- ② 三发并行展开(各自重试;一发失败不影响其他)----
    duration_s = wish.duration_s  # 先取出来:并发协程里不碰 ORM 行(见 _expand_one)
    done = 0

    async def run(take: dict) -> dict | None:
        nonlocal done
        cand = await _expand_one(take, style, duration_s, context, memories)
        done += 1
        # 完成计数式进度:并发下「第几个」没有意义,报「已出几个」才对得上用户看到的卡片数
        progress(f"已出 {done}/{len(takes)} 个本子…")
        return cand

    results = await asyncio.gather(*(run(t) for t in takes))
    candidates = [c for c in results if c]
    if len(candidates) < _MIN_CANDIDATES:
        raise BirthdayBatchError("候选本子过少,请重试。")
    if len(candidates) < len(takes):
        logger.warning("%d/%d 条切入展开失败,按现有候选交付", len(takes) - len(candidates), len(candidates))

    wish.style_name = style["style_name"]
    wish.style_cn = style["style_cn"]
    wish.style_en = style["style_en"]
    wish.negative = style["negative"]
    wish.candidates = candidates
    wish.chosen = -1
    wish.clip = {}
    wish.status = "generated"
    db.commit()
    return {"candidates": candidates, "style_name": wish.style_name}


async def reexpand_batch(
    db: Session, wish: BirthdayWish, index: int, feedback: str, progress=lambda s: None
) -> dict:
    """单条重拍:保持风格卡与该条切入不变,只重新展开分镜(可带用户意见)。

    换一批(整批重来)与单条重拍的分工:切入方向不对 → 换一批带意见;
    方向对但执行差(分镜平/回忆没落实)→ 重拍这条。
    重拍的是已选定的那条时,chosen 重置为未选——内容变了必须重新确认,不静默替换手卡。
    """
    candidates = list(wish.candidates or [])
    if not (0 <= index < len(candidates)):
        raise BirthdayBatchError(f"候选序号无效:{index}(共 {len(candidates)} 个)")
    old = candidates[index]
    take = {
        "take": old.get("take") or f"切入{index + 1}",
        "logline": old.get("logline") or "",
        "emotion_curve": old.get("emotion_curve") or "",
        "punchline": old.get("punchline") or "",
        "hook_text": old.get("hook_text") or "",
    }
    style = {
        "style_cn": wish.style_cn or "",
        "style_en": wish.style_en or "",
        "negative": wish.negative or "",
    }
    context = _build_context(wish)
    memories = [str(m).strip() for m in (wish.memories or []) if str(m or "").strip()]
    progress(f"AI 正在重拍「{take['take']}」…")
    cand = await _expand_one(take, style, wish.duration_s, context, memories, feedback=feedback)
    if cand is None:
        raise BirthdayBatchError(
            f"「{take['take']}」重拍失败(分镜没出来),原候选保持不变,请重试。"
        )
    candidates[index] = cand
    wish.candidates = candidates
    if wish.chosen == index:
        # 重拍的是已选定条:旧手卡对应的内容已被替换,必须重新三选一
        wish.chosen = -1
        wish.clip = {}
        wish.status = "generated"
    db.commit()
    return {"candidates": candidates}


def pick_wish(db: Session, wish: BirthdayWish, index: int) -> dict:
    """选定第 index 个候选(0 起)为最终本子。"""
    candidates = wish.candidates or []
    if not (0 <= index < len(candidates)):
        raise BirthdayBatchError(f"候选序号无效:{index}(共 {len(candidates)} 个)")
    wish.chosen = index
    wish.clip = candidates[index]
    wish.status = "picked"
    db.commit()
    from app.engines.birthday.common import wish_dict

    return wish_dict(wish)
