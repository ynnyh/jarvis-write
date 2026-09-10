# app/engines/pipeline/chapter_compose.py
# -*- coding: utf-8 -*-
"""草稿 → 定稿:把 31 个占位符的拼接从 generate_chapter 里挪出来。

从 chapter.py 拆出。原来这段是嵌在 generate_chapter 内部的闭包 `_compose`,
因为它要捕获十个以上的外层局部变量;拆出来之后,那些变量被显式收进
`ChapterContext` 数据类——**依赖一目了然,也顺带消灭了闭包隐式捕获导致的
「改一处忘了另一处」类 bug**。

`CHAPTER_DRAFT_PROMPT` 的 31 个占位符在这里一次性喂满(见 docs/14 诊断①:
校验厚、创作薄;这一层的价值是把「创作」那次调用的输入准备好)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.engines.common import chapter_architecture_brief
from app.engines.pipeline.tension_bus import tension_bus_block
from app.engines.polish.polisher import _flavor_hits_block
from app.engines.polish import ai_flavor_report
from app.prompts.chapter import CHAPTER_DRAFT_PROMPT, CHAPTER_FINALIZE_PROMPT
from app.prompts.style_capsules import pairwise_examples_block
from app.llm.router import Task, get_adapter_for


def _strip_meta(text: str) -> str:
    """清理模型输出的元信息:开头的 markdown 标题行 / 章节标题行。"""
    import re

    chap_title = re.compile(
        r"^第[0-9零一二三四五六七八九十百千两]+章"
        r"([\s:：、·.。\-—《（(]*.{0,20})?$"
    )
    lines = text.strip().splitlines()
    while lines:
        head = lines[0].strip()
        if head.startswith("#") or (len(head) < 25 and chap_title.match(head)):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


@dataclass
class ChapterContext:
    """草稿/定稿所需的全部上下文。字段即 prompt 的实参来源。

    注意 style_block / deai_rules 是「运行时算出来的」而非静态:
    style_block 在组装过程中被反复追加(文风备忘/手法卡/范本/DNA/疲劳词/雷区),
    顺序有语义(后面的覆盖前面的),不要挪动调用方的追加顺序。
    """

    chapter_number: int
    outline: Any
    next_outline: Any
    style_block: str
    rolling: str
    recent: str
    handoff_block: str
    hard_constraints: str
    known_roster: str
    resource_ledger: str
    foreshadow_reminders: str
    device_reminders: str
    avoid_repetition: str
    twist_prep: str
    deai_rules: str
    project: Any = None
    db: Session | None = None


class Composer:
    """把 ChapterContext 变成「能反复重写」的 compose 回调。

    用法:
        composer = Composer(ctx)
        draft, final = await composer("")             # 首轮
        draft, final = await composer(rev_block)      # 回炉
    场景级模式下首轮已由 compose_by_scenes 完成,构造时传 precomputed=(draft, final),
    此后只有带 rev_block 的调用才真正调 LLM(整章重写)——因为回炉意见是针对
    整章的(逐场重写无法响应「第 3 段和第 7 段互相矛盾」这类意见)。
    """

    def __init__(
        self,
        ctx: ChapterContext,
        *,
        precomputed: tuple[str, str] | None = None,
    ) -> None:
        self.ctx = ctx
        self._precomputed = precomputed

    def _draft_prompt(self, rev_block: str) -> str:
        ctx = self.ctx
        outline = ctx.outline
        project = ctx.project
        return CHAPTER_DRAFT_PROMPT.format(
            chapter_number=ctx.chapter_number,
            chapter_title=outline.title,
            drama_task=_drama_task_block(outline),
            tension_bus_block=tension_bus_block(
                ctx.chapter_number,
                target_chapters=int(project.target_chapters or 0),
                macro_plan=project.macro_plan,
                chapter_role=str(outline.chapter_role or ""),
                suspense_level=str(outline.suspense_level or ""),
            ),
            architecture_brief=chapter_architecture_brief(project),
            rolling_summary=ctx.rolling,
            recent_tail=ctx.recent,
            handoff_contract=ctx.handoff_block,
            hard_constraints=ctx.hard_constraints,
            known_roster=ctx.known_roster,
            resource_ledger=ctx.resource_ledger,
            foreshadow_reminders=ctx.foreshadow_reminders,
            device_reminders=ctx.device_reminders,
            avoid_repetition=ctx.avoid_repetition,
            revision_block=rev_block,
            twist_prep=ctx.twist_prep,
            chapter_role=outline.chapter_role,
            chapter_purpose=outline.chapter_purpose,
            suspense_level=outline.suspense_level,
            foreshadowing=outline.foreshadowing,
            characters_involved="、".join(map(str, outline.characters_involved)) or "(未指定)",
            key_items="、".join(map(str, outline.key_items)) or "无",
            scene_location=outline.scene_location,
            chapter_summary=outline.summary,
            chapter_beats=_beats_block(outline),
            next_chapter_brief=_next_chapter_brief(ctx.next_outline),
            word_number=project.target_words_per_chapter,
            word_floor=project.target_words_per_chapter * 4 // 5,
            word_ceil=project.target_words_per_chapter * 6 // 5,
            scene_count=max(2, project.target_words_per_chapter // 1000),
            scene_words=project.target_words_per_chapter // max(2, project.target_words_per_chapter // 1000),
            style_directives=ctx.style_block,
            deai_rules=ctx.deai_rules,
        )

    def _finalize_prompt(self, draft: str) -> str:
        ctx = self.ctx
        outline = ctx.outline
        project = ctx.project
        # 定稿前的去味诊断(纯规则零成本):草稿先过 AI 味检测,命中句贴进定稿
        # prompt 定点改写 —— 复用润色端"先诊断后治疗"的成熟模式,生成端不再只靠自觉。
        flavor_hits = _flavor_hits_block(ai_flavor_report(draft))
        return CHAPTER_FINALIZE_PROMPT.format(
            chapter_number=ctx.chapter_number,
            chapter_title=outline.title,
            chapter_purpose=outline.chapter_purpose,
            drama_task=_drama_task_block(outline),
            tension_bus_block=tension_bus_block(
                ctx.chapter_number,
                target_chapters=int(project.target_chapters or 0),
                macro_plan=project.macro_plan,
                chapter_role=str(outline.chapter_role or ""),
                suspense_level=str(outline.suspense_level or ""),
            ),
            foreshadowing=outline.foreshadowing,
            chapter_summary=outline.summary,
            rolling_summary=ctx.rolling,
            known_roster=ctx.known_roster,
            resource_ledger=ctx.resource_ledger,
            draft_text=draft,
            flavor_hits=flavor_hits,
            # 定稿额外注入「AI 腔→人话」配对反例(给 pattern 比给 rule 有效);草稿不注入
            # 以控 token(草稿还没成文,无从对照,正向锚 voice 已在 style_block 里够用)
            style_directives=ctx.style_block + pairwise_examples_block(),
        )

    async def __call__(
        self, rev_block: str, draft_label: str = "1/6 生成草稿",
        finalize_label: str = "2/6 定稿修订", report=None,
    ) -> tuple[str, str]:
        """草稿 → 定稿。返回 (draft, final)。"""
        if self._precomputed is not None and not rev_block:
            return self._precomputed
        if report:
            report(draft_label)
        d = _strip_meta(
            await get_adapter_for(Task.DRAFT).ask(self._draft_prompt(rev_block))
        )
        if report:
            report(finalize_label)
        f = _strip_meta(
            await get_adapter_for(Task.FINALIZE).ask(self._finalize_prompt(d))
        )
        return d, f


__all__ = ["ChapterContext", "Composer", "_strip_meta"]


# ---- 以下从 chapter.py 原样搬来(与 compose 强耦合的渲染 helper) ----

def _beats_block(outline) -> str:
    """把本章场景节拍渲染成草稿的施工清单;无节拍时提示模型自行拆分。"""
    beats = [str(b).strip() for b in (outline.beats or []) if str(b).strip()]
    if not beats:
        return "(本章未预设节拍,请自行把剧情拆成若干有起伏的场景,不要平铺直叙)"
    lines = "\n".join(f"  {i}. {b}" for i, b in enumerate(beats, 1))
    return (
        "按以下场景节拍逐个推进(每个节拍写成一个有画面、有张力的场景,"
        "顺序可微调,但都要落实):\n" + lines
    )


def _next_chapter_brief(nxt) -> str:
    if nxt is None:
        return "(本章为最后一章,收束全书)"
    return (
        f"第{nxt.chapter_number}章《{nxt.title}》:{nxt.summary}"
        f"(伏笔操作:{nxt.foreshadowing})"
    )


# 反 AI 腔扩展规则的触发线
_DEAI_ESCALATE_HITS = 8


def _deai_rules_block(recent_texts: list[str]) -> str:
    """反 AI 腔规则分级注入:核心版永远在,扩展版只在最近几章确实脏时追加。

    与 fatigue_block 分工:那边管「本书特有的高频词」,这边管「通用禁令的力度」。
    全书干净时只给核心版——平时把十几条禁令全摆出来,模型会把力气全花在
    「不出错」上,写出来的就平。
    """
    from app.prompts.chapter import _DEAI_CORE, _DEAI_EXTRA

    hits = 0
    for text in recent_texts:
        hits += sum(c["count"] for c in ai_flavor_report(text).categories.values())
    if hits >= _DEAI_ESCALATE_HITS:
        return _DEAI_CORE + _DEAI_EXTRA
    return _DEAI_CORE


# ---- 本章戏剧任务:把「写什么」的注意力补回来 ----
# 蓝图只交代「发生了什么」,没人告诉模型「这一章要让读者感受到什么」。而形式
# 约束(反 AI 腔 + 文风质感)长期占掉草稿模板近半篇幅,净信号就成了「别出格、
# 别用力」——生成结果正是「每章都工整、都挑不出毛病,但都像喝白水」。
# 这里用蓝图已有字段(定位/悬念密度/认知颠覆)做确定性推导,零额外 LLM 调用。
_ROLE_TASKS: tuple[tuple[str, str], ...] = (
    ("高潮", "本章是这一段的总爆发,前面攒的力要在这里兑现。把最强的场面压在中后段,"
             "别一上来就用满,让读者一路提到顶点再砸下来。"),
    ("转折", "本章必须把读者已经相信的某件事掀翻。前 2/3 铺垫得越稳、越像那么回事,"
             "翻的那一刻才越响;不要提前泄底,也不要翻完立刻解释。"),
    ("危机", "本章要把主角逼到没有退路的境地。让读者跟着他一起难受、一起想办法,"
             "困境别轻轻就解开——轻易脱身的危机等于没危机。"),
    ("铺垫", "本章可以压着写,但压是为了后面弹得更高。压不等于没味道:埋一个让人"
             "不安的细节、一句后来才懂的话,让平静底下有东西在走。"),
    ("过渡", "本章承上启下,篇幅可以收着,但必须完成一件事:让读者对下一章产生"
             "具体的期待,而不是读完毫无牵挂。"),
    ("结局", "本章收束全书:该还的债要还,人物要有落点。收得干脆,别拖泥带水,"
             "也别急着把一切解释清楚。"),
)
_DEFAULT_ROLE_TASK = (
    "本章要往前推一格:让读者读完时,人物的处境或心境与开头相比确实变了,"
    "而不是原地走了一圈。"
)
_SUSPENSE_TASKS: tuple[tuple[str, str], ...] = (
    ("高", "每 300-500 字就要有一个勾着读者往下看的东西——一个疑问、一个反常、"
           "一句没说完的话。"),
    ("中", "章内至少两处让读者心里一紧的地方,别一路平推到底。"),
    ("低", "可以写得从容,但章末必须留下一个具体的悬念,不是一句空泛的感叹。"),
)


def _drama_task_block(outline) -> str:
    """本章的戏剧任务:这一章要让读者的情绪往哪走。

    确定性推导,不给模型加任何调用。治「文绉绉、喝白水」——每章先定调、定戏核,
    再动笔。
    """
    role = str(outline.chapter_role or "")
    task = _DEFAULT_ROLE_TASK
    for key, value in _ROLE_TASKS:
        if key in role:
            task = value
            break
    lines = [f"- 本章任务({role or '未指定定位'}):{task}"]

    suspense = str(outline.suspense_level or "")
    for key, value in _SUSPENSE_TASKS:
        if key in suspense:
            lines.append(f"- 悬念密度({suspense}):{value}")
            break

    # 认知颠覆在蓝图里是「1-5 星」(如 ★★★★☆),也可能被写成「强/高」字样
    twist = str(outline.plot_twist_level or "")
    if "强" in twist or "高" in twist or twist.count("★") >= 4:
        lines.append(
            f"- 认知颠覆({twist}):必须有一个推翻读者既有判断的时刻,"
            "且要在正文里给出足以支撑它的细节,不能靠人物嘴上说破。"
        )

    # 这三条与定位无关,任何一章都该有——「戏核」「基调」「落差」是治白水的三件套
    # 调子与戏核:蓝图定过就照蓝图执行(那是全书编排),老蓝图没定过才让模型自定
    tone = str(getattr(outline, "emotional_tone", "") or "").strip()
    if tone:
        lines.append(
            f"- 情绪基调(蓝图已定:{tone}):全章的场景、对话节奏、细节选择都贴着这个"
            "调子走;要换调必须是有意为之的转折,不是写着写着跑掉了。"
        )
    else:
        lines.append(
            "- 情绪基调:先给本章定一个调子(压抑/紧绷/荒诞/温热/悲凉/亢奋…),场景、"
            "对话节奏、细节选择都贴着这个调子走;换调必须是有意为之的转折,"
            "不是写着写着跑掉了。"
        )
    anchor = str(getattr(outline, "scene_anchor", "") or "").strip()
    if anchor:
        lines.append(
            f"- 本章戏核(蓝图已定):{anchor}。全章围着这一瞬铺,其余都是铺垫——"
            "别把力气平均分给每一个段落。"
        )
    else:
        lines.append(
            "- 本章戏核:动笔前先定一个让读者记住的瞬间(一句话,如「他终于认出那道疤」),"
            "全章围着它铺;没有戏核的章,写得再工整也是白水。"
        )
    lines.append(
        "- 情绪落差:章内必须有起落。该精彩的段落放开写,该压抑的段落压住写——"
        "全章一个温度,读者就会走神。"
    )
    return "\n".join(lines)
