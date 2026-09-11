# app/engines/pipeline/scene_write.py
# -*- coding: utf-8 -*-
"""场景级生成与验收:一场景一次调用,不通过只重写这一场。

结构性诊断之三:「章级单发、无场景级分解」。
此前 beats 只是塞进 prompt 的提示文本,不是执行单元——一章仍是一次生成。
逐场景生成带来三件事:

  ① 注意力收窄:每次调用只带一个情绪指令 + 一组检索到的事实 + 相邻上下文,
     模型不必在 31 个变量之间做妥协,「该精彩时放开」才落得了地。
  ② 定点验收:验收只读一场,不合格只重写这一场。章级回炉是「整章重新抽签」,
     实测 prose 维 6→6→6 烧满预算纹丝不动;场景级回炉的着力面小得多。
  ③ 可干预:作者能在某一场上直接接手,而不必等整章出完。

缝接策略(逐场生成的固有风险):场与场之间容易「像断开的两个片段」。这里不走
LLM 缝接——缝接是排版问题不是创作问题。做法是:
  · 上一场尾部原文注入下一场的 prompt(衔接有一手材料);
  · 场景卡里「第一条要交代与上一场的时间/场景衔接」由 prompt 硬要求;
  · 拼接用字符 offset 精确记录每个场景的 [start, end),不做字符串搜索
    (重复段落会让搜索定位歧义)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Project, Scene, SceneVersion
from app.engines.common import SCOPE_SCENE_ACCEPT
from app.engines.pipeline.retrieval import (
    render_facts_block,
    render_tail_block,
    retrieve_for_scene,
)
from app.engines.pipeline.scene_plan import tension_directive
from app.llm.router import Task, get_adapter_for
from app.prompts.scene import (
    SCENE_ACCEPT_PROMPT,
    SCENE_DRAFT_PROMPT,
    SCENE_JOIN_SEPARATOR,
)

logger = logging.getLogger("jarvis-write.scene_write")

# 上一场尾部注入下一场的长度:够模型接住语感与在场人物,不至于把上一场重写一遍
_PREV_TAIL_CHARS = 600

# 场景重写封顶(D4):连修 2 次仍不过就停手,把「这一场到底是什么问题」交给用户
# 与章级回炉的判断。不设更高是因为场景级重写的收益递减很快——第 3 次基本是
# 同一份 prompt 再抽一次签。
MAX_SCENE_REWRITES = 2

# 场景验收的达标线:与项目级阈值同口径(五维 >= threshold),但场景只判五项中的
# 三项硬指标——emotion_fit/goal_done/concreteness 是「这一场该给的给了没有」,
# 而 tension_fit/prose 交给章级主审统一判(避免同一件事被两处判、两处不一致)
_SCENE_HARD_DIMS = ("emotion_fit", "goal_done", "concreteness")


@dataclass
class SceneWriteResult:
    """一章的场景级生成结果。"""

    text: str = ""
    scenes: list[Scene] = field(default_factory=list)
    # 每个场景的 [start, end) 字符区间,与 scenes 同序
    anchors: list[tuple[int, int]] = field(default_factory=list)
    # 逐场验收记录(前端展示 + 评测消费)
    verdicts: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _record_retrieval_usage(
    db: Session,
    project: Project,
    scene: Scene,
    chapter_number: int,
    ctx: Any,
    revision_directive: str,
) -> None:
    """把「本场把这几条事实喂进了 prompt」记进消费日志(§1.5)。

    失败一律吞掉:记日志是观测,不是生成的前置条件——不能在真库上多一条
    引用记录这件事上把整章生成搞挂(与检索本身的容错口径一致)。

    重写路径不记:那条 prompt 里没有 facts_block,模型没看到这些事实,
    记了会让失效传播时误报「这一章真的依赖它」。
    """
    if revision_directive:
        return
    try:
        from app.engines.consistency.fact_ledger import record_retrieval

        record_retrieval(db, int(project.id), scene, list(getattr(ctx, "facts", None) or []))
    except Exception as exc:  # noqa: BLE001
        logger.debug("场景事实消费日志写入失败(忽略):%s", exc)


def _tail_of(text: str, limit: int = _PREV_TAIL_CHARS) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return "……" + t[-limit:]


def _word_target_line(project_target: int, scene: Scene) -> str:
    """本场字数区间行。以场景卡自己的 target_words 为准(章目标在切分时已摊过)。"""
    target = int(scene.target_words or 0) or (project_target // 4 or 1500)
    floor = max(600, target * 2 // 3)
    ceil = target * 5 // 4
    return f"本场目标 {target} 字(下限 {floor},上限 {ceil})"


def _twist_prep(
    db: Session,
    project_id: int,
    chapter_number: int,
    *,
    outline: Any = None,
    scene: Scene | None = None,
) -> str:
    """反转预备块的取用(§1.4)。

    只在**转折章**才去查读者认知地图——非转折章查了也是空串,但省掉两次库查询。
    某个具体场景是否承接反转,由场景卡的 conflict 里是否含转折信号决定:
    逐场下发能让反转落在「该掀翻的那一场」,而不是整章每场都收到同一段提示
    (那等于把注意力预算又摊平了,与本模块的初衷相反)。
    """
    from app.engines.consistency.reader_knowledge import (
        build_reader_view,
        is_twist_chapter,
        render_twist_block,
    )

    if outline is not None and not is_twist_chapter(outline):
        return ""
    view = build_reader_view(db, project_id, chapter_number, outline=outline)
    if scene is not None and outline is not None:
        # 逐场收窄:只有承接反转线索的那一场才注入。scene.conflict 是切分时
        # 从蓝图里派生的「张力来源」,含转折词说明这一场就是掀翻点。
        blob = f"{scene.conflict or ''}{scene.summary or ''}"
        if not any(w in blob for w in _TWIST_ROLE_WORDS):
            return ""
    return render_twist_block(view, db)


# 与 reader_knowledge._TWIST_ROLE_WORDS 同源:场景卡的 conflict/summary 里出现这些词,
# 说明这一场就是反转的落点。此处只做「哪一场」的分流,判「是不是转折章」仍以蓝图为准。
_TWIST_ROLE_WORDS = ("反转", "揭露", "真相", "颠覆", "转折", "揭晓", "败露")


async def write_scene(
    db: Session,
    project: Project,
    scene: Scene,
    *,
    chapter_number: int,
    scene_total: int,
    style_block: str,
    deai_rules: str,
    rolling_summary: str,
    recent_tail: str,
    handoff_block: str,
    scene_anchor: str,
    chapter_summary: str,
    chapter_title: str,
    previous_text: str = "",
    revision_directive: str = "",
    outline: Any = None,
) -> str:
    """写一个场景,返回正文。

    检索在这里发生:本场需要的事实由 retrieve_for_scene 现场捞出,而不是把
    全库灌进来。上一场的尾部也一并注入(缝接的一手材料)。
    """
    ctx = retrieve_for_scene(db, project.id, scene, chapter_number)
    facts_block = render_facts_block(ctx)
    tail_extra = render_tail_block(ctx)
    # 消费日志(§1.5):把「这一场确实看到了这几条事实」记下来。
    # 只有在真正要把事实喂进 prompt 时才记(重写路径的 prompt 不带 facts_block,
    # 记了就是假账)。放在这里而非 retrieve_for_scene 内,是因为「检索」与
    # 「注入」是两回事:检索到了但没注入(如重写路径)不该算消费。
    _record_retrieval_usage(db, project, scene, chapter_number, ctx, revision_directive)

    if revision_directive:
        from app.prompts.scene import _SCENE_REWRITE_PROMPT

        prompt = _SCENE_REWRITE_PROMPT.format(
            scene_title=scene.title or f"第{scene.seq}场",
            scene_location=scene.location or "(未指定)",
            scene_characters="、".join(map(str, scene.characters or [])) or "(未指定)",
            scene_goal=scene.goal or "(未指定)",
            scene_conflict=scene.conflict or "(未指定)",
            emotion_target=scene.emotion_target or "(未指定)",
            tension_directive=tension_directive(scene.tension_level),
            revision_directive=revision_directive,
            previous_text=_tail_of(scene.content, 2000),
            scene_words=int(scene.target_words or 0) or 1500,
        )
        scene.status = "drafting"
        db.flush()
        raw = await get_adapter_for(Task.SCENE_DRAFT).ask(prompt)
        return _strip_scene_meta(raw)

    prompt = SCENE_DRAFT_PROMPT.format(
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        chapter_summary=chapter_summary or "",
        scene_anchor=scene_anchor or "(未指定)",
        scene_seq=scene.seq,
        scene_total=scene_total,
        scene_title=scene.title or f"第{scene.seq}场",
        scene_location=scene.location or "(未指定)",
        scene_characters="、".join(map(str, scene.characters or [])) or "(未指定)",
        scene_goal=scene.goal or "(未指定)",
        scene_conflict=scene.conflict or "(未指定)",
        emotion_target=scene.emotion_target or "(未指定)",
        tension_directive=tension_directive(scene.tension_level),
        twist_prep=_twist_prep(
            db, project.id, chapter_number, outline=outline, scene=scene
        ),
        scene_word_target=_word_target_line(int(project.target_words_per_chapter or 0), scene),
        scene_words=int(scene.target_words or 0) or 1500,
        scene_word_floor=max(600, (int(scene.target_words or 0) or 1500) * 2 // 3),
        scene_word_ceil=(int(scene.target_words or 0) or 1500) * 5 // 4,
        hard_constraints=facts_block,
        resource_ledger="",
        rolling_summary=rolling_summary or "",
        recent_tail=recent_tail or "",
        handoff_contract=handoff_block or "",
        previous_scene_tail=(
            # 用「上一场结尾」这个词而不是「上一场尾部」:与 prompt 正文里的措辞一致,
            # 便于阅读与检索;上一场为空(首场或上一场生成失败)时整块不出现。
            f"【上一场结尾(直接接住,不要重述)】\n{_tail_of(previous_text)}\n"
            if (previous_text or "").strip()
            else ""
        ),
        style_directives=style_block or "",
        deai_rules=deai_rules or "",
    )
    scene.status = "drafting"
    db.flush()
    raw = await get_adapter_for(Task.SCENE_DRAFT).ask(prompt)
    text = _strip_scene_meta(raw)
    # 检索过来的一手材料也随正文存一份,前端「这一场参考了什么」可回显
    scene.accept_scores = {**(scene.accept_scores or {}), "retrieval": ctx.stats}
    if tail_extra:
        scene.accept_scores["tail_excerpts"] = ctx.stats.get("tail_texts") or []
    db.flush()
    return text


def _strip_scene_meta(text: str) -> str:
    """清掉模型偶尔带出的元信息:场景小标题、markdown 标题、"第N场:xxx" 之类。

    这个函数踩过一次坑,值得把边界写清楚。

    初版是「只要首行像短标题就删」:既按 `^第\\s*[0-9一二三四五六七八九十]+\\s*场`
    一刀切,又拿「长度 <= 20 且无句末标点」当判据。结果是「第一场。」「第一场正文内容。」
    这类正常的叙事首句被整句吃掉,正文直接变空——而这类句子恰恰是场景级生成最想要的
    「短促有力」。教训:判据必须落在「这一行是元信息」而不是「这一行看起来短」。

    现在的规则:
      · 明星形态(直接删,不论后面有没有正文):`## 标题`、`第3场 - 祭坛撞破`、
        `第3场:祭坛撞破`、`第3场《祭坛撞破》`、`【祭坛撞破】`/`（祭坛撞破）`;
      · 长度启发式(仅当后面还有正文时才删,且一个字的行不算)——整段只有一行时,
        那一行就是正文,删了等于把这一场清空;
      · 带句末标点的一律当正文留下。
    """
    import re

    # 元信息的小标题形态:
    #   "第3场 - 祭坛撞破" / "第3场:祭坛撞破" / "第3场·祭坛撞破"
    #   "第3场《祭坛撞破》" / "第3场【祭坛撞破】"
    #   "【祭坛撞破】" / "（祭坛撞破）" / "## 祭坛撞破"
    _SEQ_TITLE = re.compile(
        r"^第\s*[0-9一二三四五六七八九十]+\s*场\s*[-—–:：·、\-]\s*\S+$"
    )
    _SEQ_BARE_TITLE = re.compile(
        r"^第\s*[0-9一二三四五六七八九十]+\s*场\s*[《「【].+[》」】]$"
    )
    _BRACKET_TITLE = re.compile(r"^[\[【(（].{1,30}[\]】)）]$")
    _SENTENCE_END = re.compile(r"[。!?!?…]")
    # 无句末标点的短行,长度上限;一个字的行不参与(那是正文)
    _BARE_TITLE_MAX = 16

    def _looks_like_bare_title(s: str) -> bool:
        return (
            1 < len(s) <= _BARE_TITLE_MAX
            and not _SENTENCE_END.search(s)
        )

    def _is_marker(s: str) -> bool:
        return bool(
            s.startswith("#")
            or _SEQ_TITLE.match(s)
            or _SEQ_BARE_TITLE.match(s)
            or _BRACKET_TITLE.match(s)
        )

    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    # 第一轮:后面还有正文,可以放心地把标题行剥掉(含长度启发式)
    while len(lines) > 1:
        head = lines[0]
        if _is_marker(head) or _looks_like_bare_title(head):
            lines.pop(0)
            continue
        break
    # 第二轮:只剩一行,只剥明星形态,不拿长度猜
    while lines and _is_marker(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _parse_verdict(raw: str) -> dict[str, Any]:
    """场景验收输出 → dict。解析失败抛 ValueError,由调用方降级。"""
    from app.engines.common import parse_llm_json_checked

    data, err = parse_llm_json_checked(raw)
    if err:
        raise ValueError(err)
    scores = data.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("缺少 scores 字段")
    return data


async def accept_scene(
    db: Session, scene: Scene, threshold: int
) -> dict[str, Any]:
    """验收一个场景。返回 verdict dict(含 passed)。

    降级处理:模型输出解析失败 → passed 保持 False 但记 degraded 标记,
    **不计入重写**(重写解决不了解析问题,只会白烧钱)——与章级主审降级同一口径。
    """
    prompt = SCENE_ACCEPT_PROMPT.format(
        scene_title=scene.title or f"第{scene.seq}场",
        scene_location=scene.location or "(未指定)",
        scene_characters="、".join(map(str, scene.characters or [])) or "(未指定)",
        scene_goal=scene.goal or "(未指定)",
        scene_conflict=scene.conflict or "(未指定)",
        emotion_target=scene.emotion_target or "(未指定)",
        tension_level=scene.tension_level,
        tension_directive=tension_directive(scene.tension_level),
        scene_text=scene.content or "",
    )
    try:
        raw = await get_adapter_for(Task.SCENE_ACCEPT).ask(prompt)
        verdict = _parse_verdict(raw)
    except Exception as exc:  # noqa: BLE001 — 验收失败不该阻断生成
        logger.warning("第 %d 场验收降级:%s", scene.seq, exc)
        return {
            "passed": False,
            "degraded": True,
            "scope": SCOPE_SCENE_ACCEPT,
            "reason": str(exc)[:150],
            "scores": {},
            "comment": "本场验收未能完成(输出解析失败),已跳过本场判定",
            "suggestions": [],
        }

    scores = {k: int(v) for k, v in verdict["scores"].items() if _as_int(v) is not None}
    failing = [d for d in _SCENE_HARD_DIMS if scores.get(d, 0) < threshold]
    return {
        "passed": not failing,
        "degraded": False,
        "scores": scores,
        "failing": failing,
        "comment": str(verdict.get("comment") or "")[:400],
        "suggestions": verdict.get("suggestions") or [],
    }


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):  # 转不成整数返回 None,调用方按缺失处理
        return None


def _build_scene_directive(verdict: dict) -> str:
    """场景验收意见 → 重写指令文本。"""
    parts: list[str] = []
    if verdict.get("comment"):
        parts.append(f"总评:{verdict['comment']}")
    for s in verdict.get("suggestions") or []:
        if not isinstance(s, dict):
            continue
        bits = []
        if s.get("evidence"):
            bits.append(f"原文「{s['evidence']}」")
        if s.get("issue"):
            bits.append(f"问题:{s['issue']}")
        if s.get("fix"):
            bits.append(f"改法:{s['fix']}")
        if bits:
            parts.append(";".join(bits))
    if not parts:
        return "本场验收未通过,请重写:把该给的画面给足,情绪外化到动作与对白里。"
    return "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))


def snapshot_scene(db: Session, scene: Scene, *, source: str, note: str = "") -> None:
    """给场景存一版快照(重写前调用)。版本号递增。"""
    from sqlalchemy import func

    last = (
        db.query(func.max(SceneVersion.version))
        .filter(SceneVersion.scene_id == scene.id)
        .scalar()
    )
    db.add(
        SceneVersion(
            scene_id=scene.id,
            version=int(last or 0) + 1,
            content=scene.content or "",
            word_count=scene.word_count or 0,
            source=source,
            note=note[:500],
        )
    )


def join_scenes(scenes: list[Scene]) -> tuple[str, list[tuple[int, int]]]:
    """拼接各场正文成整章,同时返回每个场景的 [start, end) 字符区间。

    用字符 offset 精确记录而非事后字符串搜索:场与场之间可能有相似的句子,
    搜索定位会歧义,而 offset 是拼的时候就知道的。

    正文本身是「裸的」——小标题、场号、分隔符都是生成时要求模型不要写的东西,
    这里也不替它补,以免同一处排版规则散落在两处。
    """
    parts: list[str] = []
    anchors: list[tuple[int, int]] = []
    cursor = 0
    for i, s in enumerate(scenes):
        body = (s.content or "").strip()
        if i > 0 and body:
            parts.append(SCENE_JOIN_SEPARATOR)
            cursor += len(SCENE_JOIN_SEPARATOR)
        start = cursor
        parts.append(body)
        cursor += len(body)
        anchors.append((start, cursor))
    return "".join(parts), anchors
