# app/engines/pipeline/scene_plan.py
# -*- coding: utf-8 -*-
"""场景卡切分:把蓝图里的一章切成 3-5 张场景卡。

设计要点(D2:场景边界由谁定 —— 蓝图阶段就切好,生成时只「填肉」):
  切分和写作是两种能力。让模型在同一次调用里边切边写,两边都做不好——实测
  结果是「切得很均匀、写得很平」,因为它把注意力分给了结构决策,文字上只剩
  保守解。所以切分独立成一次调用(蓝图生成后立即跑),切完的场景卡落 scenes
  表,生成阶段只按卡写肉,不再自己做结构决策。

  张力曲线(D5:全书一条总线 + 每章切段):
  每张场景卡带 tension_level(1-5),生成时直接翻译成「放开写 / 压住写」的
  力度指令。曲线由蓝图已有的 suspense_level / 认知颠覆 / 章节定位做确定性
  推导,再交给模型微调——纯确定性推导会把所有高潮章都摆成同样的波形,太机械。

零 LLM 兜底:
  场景切分调用失败(模型抽风/格式崩坏)时,用 beats 做确定性切分兜底——每个
  beat 一张卡,张力按位置做「低-中-高-中」的基础波形。宁可切得粗,也不能因为
  一次调用失败就退回「整章一发」的老路。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Outline, Project, Scene, SceneVersion
from app.engines.common import SCOPE_SCENE_PLAN, degraded_stats
from app.llm.router import Task, get_adapter_for
from app.prompts.scene import SCENE_PLAN_PROMPT

logger = logging.getLogger("jarvis-write.scene")

# 单章场景数区间。一章 3-5 场:少于 3 场等于换了个名字的「整章一发」;
# 多于 5 场单场太短(1200 字以下),模型展不开情绪。
MIN_SCENES = 3
MAX_SCENES = 5

# 单场景目标字数(D3)。1500 字是实测的甜点:够铺一个完整的情绪起伏,
# 又不至于退化成「一小章」。
DEFAULT_SCENE_WORDS = 1500
MIN_SCENE_WORDS = 900
MAX_SCENE_WORDS = 2400

_TENSION_MIN, _TENSION_MAX = 1, 5

# 张力档 → 生成时的力度指令。这是「该精彩时精彩、该压抑时压抑」的直接落点:
# 不是笼统地要求「有起伏」,而是给每一场一个明确的强弱指令。
TENSION_DIRECTIVES: dict[int, str] = {
    1: "压住写。这一场是蓄力:平静底下要有东西在走,埋一个让人不安的细节、"
       "一句后来才懂的话。不要有爆发,爆发留给后面。",
    2: "收着写,但别平。让读者感到事情在往一个方向滑,心里开始发紧。",
    3: "常规推进。把信息给足、人物关系推进一格,保持读者往下读的牵引力。",
    4: "放开写。这一场要有一个明确的情绪高点:一次正面冲突、一个揭晓、"
       "一次失控——用力砸下去,别点到为止。",
    5: "全力爆发。这一场是这一段的总兑现:最强的画面、最狠的一击、最烫的情绪"
       "都放在这里。前面攒的力在这一场一次性还清,不留手。",
}


def tension_directive(level: int) -> str:
    """张力档 → 力度指令(生成 prompt 直接用)。越界值收敛到最近的合法档。

    None/非数字视为「未指定」→ 常规推进(3 档);0、负数按「最低档」处理而非
    「未指定」——传 0 通常意味着模型想表达「最压」,按 1 档处理更贴近意图。
    """
    if level is None:
        lv = 3
    else:
        try:
            lv = int(level)
        except (TypeError, ValueError):
            lv = 3
    lv = max(_TENSION_MIN, min(_TENSION_MAX, lv))
    return TENSION_DIRECTIVES[lv]


def base_tension_wave(count: int) -> list[int]:
    """无 LLM 时的基础张力波形:起-承-转-合的低-中-高-中。

    章内高潮压在倒数第二场,末场回落收束(与 _ROLE_TASKS 里「把最强的场面压在中后段」
    同一口径;prompt 里也是这么要求模型的,波形必须自身一致,否则提示与规则打架)。

    场次少时「起承转合」摆不开,取舍如下:
      1 场 → [4]        只有一场,直接给高点(此时谈不上曲线)
      2 场 → [4, 2]     高潮在前、尾声在后:两场摆不下「先攒后爆」,不如先爆再收
      3 场 → [2, 5, 3]  攒力 → 爆发 → 回落(峰值在第 2 场,末场留悬念)
      4 场 → [1, 3, 5, 3]
      5+ 场 → 低-中-中-高-中(峰值固定在第 4 场)
    """
    if count <= 1:
        return [4]
    if count == 2:
        return [4, 2]
    if count == 3:
        return [2, 5, 3]
    if count == 4:
        return [1, 3, 5, 3]
    # 5 场及以上:低-中-中-高-中,再补的场摆在中段
    wave = [1, 3, 3, 5, 3]
    while len(wave) < count:
        wave.insert(-1, 3)
    return wave[:count]


def _has_curve(levels: list[int]) -> bool:
    """一串张力档是否构成了「有起伏」的曲线。

    判据:至少三档不同,且存在一次明显的强弱落差(相邻差 ≥2)。
    只要求「不全相同」太松——[3,3,3,3,4] 也满足,但那依然是全章一个温度。
    """
    if len(levels) < 2:
        return False
    if len(set(levels)) < 3:
        return False
    return any(abs(b - a) >= 2 for a, b in zip(levels, levels[1:]))


def _clamp_words(value: Any, chapter_target: int, count: int) -> int:
    """把模型给的单场字数收敛到合法区间,并尽量对齐章目标字数。

    章目标字数 / 场景数 = 均分基准。模型给的值偏离太远(或没给)时回落到基准,
    否则一章可能写出 8000 字(总和超标)或 2000 字(严重欠账)。
    """
    baseline = max(MIN_SCENE_WORDS, chapter_target // max(1, count)) if chapter_target else DEFAULT_SCENE_WORDS
    baseline = max(MIN_SCENE_WORDS, min(MAX_SCENE_WORDS, baseline))
    try:
        w = int(value)
    except (TypeError, ValueError):
        return baseline
    if w < MIN_SCENE_WORDS or w > MAX_SCENE_WORDS:
        return baseline
    return w


def _clamp_tension(value: Any, fallback: int) -> int:
    """模型给的张力档 → 合法档。缺失/脏值用 fallback;0 与越界值收敛到最近合法档。"""
    if value is None:
        return fallback
    try:
        lv = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(_TENSION_MIN, min(_TENSION_MAX, lv))


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        import re

        return [p for p in re.split(r"[,，、;；/]", value) if p.strip()]
    return []


def _extract_json(raw: str) -> list[dict[str, Any]]:
    """从模型输出里抠出场景数组。

    容忍三种常见形态:纯 JSON 数组 / 包在 {"scenes": [...]} 里 /
    markdown 代码块包裹。抠不到就抛 ValueError,由调用方走确定性兜底。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("空输出")
    # 去 markdown 代码围栏
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    candidates = [text]
    # 数组切片
    lb, rb = text.find("["), text.rfind("]")
    if lb != -1 and rb > lb:
        candidates.append(text[lb : rb + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            for key in ("scenes", "场景", "scene_list"):
                if isinstance(data.get(key), list):
                    return [d for d in data[key] if isinstance(d, dict)]
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    raise ValueError("未找到可解析的场景数组")


def plan_from_beats(
    outline: Outline, chapter_target: int, count: int | None = None
) -> list[dict[str, Any]]:
    """确定性兜底:按 beats 每个节拍一张卡。

    蓝图没定 beats 时(老书/蓝图崩坏),用 summary 一句话切成一个场景——
    此时场景化退化为「整章一发」,但结构上仍然走得通(不会报错、不会卡生成)。
    """
    beats = [str(b).strip() for b in (outline.beats or []) if str(b).strip()]
    if not beats:
        beats = [str(outline.summary or "").strip() or str(outline.title or "").strip()]
    # 少于 MIN_SCENES 个节拍时按最小场景数补齐:不硬造内容,只是把同一节拍
    # 拆成「起 / 承转 / 合」三段,让分段生成的结构仍然成立
    while len(beats) < MIN_SCENES and len(beats) < MAX_SCENES:
        beats = beats + [beats[-1]]
    beats = beats[:MAX_SCENES] if count is None else beats[:count]

    wave = base_tension_wave(len(beats))
    words = _clamp_words(None, chapter_target, len(beats))
    out: list[dict[str, Any]] = []
    for i, beat in enumerate(beats):
        out.append(
            {
                "title": beat[:40],
                "summary": beat,
                "location": str(outline.scene_location or ""),
                "characters": [str(c) for c in (outline.characters_involved or [])],
                "goal": "",
                "conflict": "",
                "emotion_target": str(outline.emotional_tone or ""),
                "tension_level": wave[i],
                "target_words": words,
                "fact_hints": [],
            }
        )
    return out


def _merge_planned(
    planned: list[dict[str, Any]],
    outline: Outline,
    chapter_target: int,
) -> list[dict[str, Any]]:
    """把模型给的场景数组规整成可直接落库的形状。

    - 数量收敛到 [MIN_SCENES, MAX_SCENES]
    - 张力:模型没给/给脏 → 用基础波形对应位置补
    - **整条波形太平 → 叠加基础波形**(核心修正,见下)
    - 字数:收敛到合法区间并对齐章目标
    - 章节级字段(地点/人物)缺失时从蓝图继承(模型经常漏)
    """
    planned = planned[:MAX_SCENES]
    count = len(planned)
    wave = base_tension_wave(count)
    words = _clamp_words(None, chapter_target, count)

    raw_levels = [_clamp_tension(item.get("tension_level"), wave[i]) for i, item in enumerate(planned)]
    # 模型经常「礼貌地」把所有场都给 3(或给 [3,3,4,3,3] 这种伪起伏)。这不是
    # 模型不懂,是它在没人逼的时候选择最保守的答案——正是白水的生成机制本身。
    # 判据 _has_curve 不通过时,直接用基础波形覆盖:模型的结构判断在这里不如
    # 一条硬编码的「起承转合」可靠,把创意留给文字、把结构交给确定性规则。
    if not _has_curve(raw_levels):
        levels = list(wave)
        logger.info(
            "第 %d 章场景张力曲线太平(%s),已用基础波形覆盖为 %s",
            outline.chapter_number, raw_levels, levels,
        )
    else:
        levels = raw_levels

    out: list[dict[str, Any]] = []
    for i, item in enumerate(planned):
        chars = _as_str_list(item.get("characters")) or _as_str_list(item.get("characters_involved"))
        if not chars:
            chars = [str(c) for c in (outline.characters_involved or [])]
        out.append(
            {
                "title": str(item.get("title") or "").strip()[:200],
                "summary": str(item.get("summary") or item.get("beat") or "").strip(),
                "location": str(item.get("location") or outline.scene_location or "").strip()[:200],
                "characters": chars,
                "goal": str(item.get("goal") or "").strip(),
                "conflict": str(item.get("conflict") or "").strip(),
                "emotion_target": (
                    str(item.get("emotion_target") or item.get("emotion") or "").strip()[:100]
                    or str(outline.emotional_tone or "")[:100]
                ),
                "tension_level": levels[i],
                "target_words": _clamp_words(item.get("target_words"), chapter_target, count),
                "fact_hints": _as_str_list(item.get("fact_hints")),
            }
        )
    return out


async def plan_scenes(
    db: Session,
    project: Project,
    outline: Outline,
    *,
    rewrite: bool = False,
) -> list[Scene]:
    """给一章切场景卡并落库。

    rewrite=False:本章已有场景卡时直接返回(幂等——重复点生成不重切,
    保护用户手改过的卡)。
    rewrite=True:强制重切(蓝图改过、或用户点「重新分场」)。

    任何失败都不抛:LLM 调用失败/解析失败 → 用 beats 做确定性切分兜底,
    并落一条降级标记(前端可见),保证生成链路永远走得通。
    """
    existing = (
        db.query(Scene)
        .filter(
            Scene.project_id == project.id,
            Scene.chapter_number == outline.chapter_number,
        )
        .order_by(Scene.seq)
        .all()
    )
    if existing and not rewrite:
        return [s for s in existing if s.status != "discarded"]

    if rewrite and existing:
        # 重切:旧卡软删(留待回溯),不物理删除——用户可能想对比切法
        for s in existing:
            s.status = "discarded"
        db.flush()

    chapter_target = int(project.target_words_per_chapter or 0)
    planned: list[dict[str, Any]] = []
    degraded = False
    try:
        prompt = SCENE_PLAN_PROMPT.format(
            chapter_number=outline.chapter_number,
            chapter_title=outline.title,
            chapter_role=outline.chapter_role or "(未指定)",
            chapter_purpose=outline.chapter_purpose or "",
            emotional_tone=outline.emotional_tone or "(未指定)",
            suspense_level=outline.suspense_level or "(未指定)",
            plot_twist_level=outline.plot_twist_level or "(未指定)",
            scene_anchor=outline.scene_anchor or "(未指定)",
            chapter_summary=outline.summary or "",
            beats_block=_beats_for_prompt(outline),
            word_number=chapter_target,
            scene_words=chapter_target // 4 if chapter_target else DEFAULT_SCENE_WORDS,
            min_scenes=MIN_SCENES,
            max_scenes=MAX_SCENES,
        )
        raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
        planned = _merge_planned(_extract_json(raw), outline, chapter_target)
    except Exception as exc:  # noqa: BLE001 — 切分失败必须兜底,不能阻断生成
        logger.warning(
            "第 %d 章场景切分失败,回落到节拍切分:%s", outline.chapter_number, exc
        )
        degraded = True
        planned = []

    if not planned:
        degraded = True
        planned = plan_from_beats(outline, chapter_target)

    rows: list[Scene] = []
    for i, item in enumerate(planned, 1):
        scene = Scene(
            project_id=project.id,
            outline_id=outline.id,
            chapter_number=outline.chapter_number,
            seq=i,
            status="planned",
            **item,
        )
        db.add(scene)
        rows.append(scene)
    db.flush()

    for scene in rows:
        db.add(
            SceneVersion(
                scene_id=scene.id,
                version=1,
                content="",
                word_count=0,
                source="planned",
                note="场景切分" + ("(节拍兜底)" if degraded else ""),
            )
        )
    db.flush()

    if degraded:
        # 降级可见(与门禁降级同一套口径):挂在首场卡的验收字段里,前端据此
        # 显示「本次分场是兜底的」,而不是静默当成正常切分。
        rows[0].accept_scores = degraded_stats(
            SCOPE_SCENE_PLAN, "场景切分失败,已按节拍兜底分场"
        )
        db.flush()
        logger.info("第 %d 章按节拍兜底分出 %d 场", outline.chapter_number, len(rows))

    logger.info(
        "第 %d 章场景切分完成:%d 场,张力=%s",
        outline.chapter_number,
        len(rows),
        [s.tension_level for s in rows],
    )
    return rows


def _beats_for_prompt(outline: Outline) -> str:
    beats = [str(b).strip() for b in (outline.beats or []) if str(b).strip()]
    if not beats:
        return "(本章未预设节拍,请依据本章简述自行切分场景)"
    return "\n".join(f"  {i}. {b}" for i, b in enumerate(beats, 1))


def scenes_of_chapter(db: Session, project_id: int, chapter_number: int) -> list[Scene]:
    """取一章的有效场景卡(按 seq 排序,已废弃的不算)。"""
    return (
        db.query(Scene)
        .filter(
            Scene.project_id == project_id,
            Scene.chapter_number == chapter_number,
            Scene.status != "discarded",
        )
        .order_by(Scene.seq)
        .all()
    )
