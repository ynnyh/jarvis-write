# app/engines/pipeline/architecture.py
# -*- coding: utf-8 -*-
"""小说顶层架构生成:雪花写作法四步串行。

种子 → 角色动力学 → 世界观 → 情节架构,每步产出作为下一步输入。
结果落库到 architecture 表(见 docs/02-data-model.md)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Architecture, Project
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.base import LLMAdapter, LLMMessage
from app.llm.router import Task, get_adapter_for
from app.prompts import (
    ARCH_CHAT_SYSTEM_PROMPT,
    ARCH_DISTILL_PROMPT,
    CHARACTER_DYNAMICS_PROMPT,
    CORE_SEED_PROMPT,
    PLOT_ARCHITECTURE_PROMPT,
    WORLD_BUILDING_PROMPT,
)
from app.schemas.concept import Concept, coerce_concept
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.pipeline")


def _render_topic_block(topic: str, concept: Concept | dict | None) -> str:
    """把结构化概念渲染成喂给核心种子的富文本;无概念时回落 topic 一句话。

    向后兼容:存量项目 concept 为 None,只有 topic。有概念但字段全空时也回落。
    """
    c = concept if isinstance(concept, Concept) else coerce_concept(concept)
    if not c.is_empty():
        return c.render()
    return (topic or "").strip() or "(自由发挥)"


def _render_directive_block(directive: str | None) -> str:
    """把「架构研讨」对话共识渲染成注入四步 prompt 的额外指令块。

    为空时返回空串(四步 prompt 里 {directive_block} 就地消失,行为与旧版一致)。
    非空时高优先级注入:反复重生成仍不满意,说明用户脑子里有具体想法没传进来,
    这块就是那些想法的落点。
    """
    directive = (directive or "").strip()
    if not directive:
        return ""
    return (
        "【作者的额外要求(最高优先级,务必在本步落实;与上面通用要求冲突时以此为准)】\n"
        f"{directive}\n\n"
    )


@dataclass
class ArchitectureResult:
    core_seed: str
    character_dynamics: str
    world_building: str
    plot_architecture: str

    @property
    def full_text(self) -> str:
        """四步拼成完整架构文本,供蓝图生成使用。"""
        return (
            f"【核心种子】\n{self.core_seed}\n\n"
            f"【角色动力学】\n{self.character_dynamics}\n\n"
            f"【世界观】\n{self.world_building}\n\n"
            f"【情节架构】\n{self.plot_architecture}"
        )


async def generate_architecture(
    *,
    topic: str,
    genre: str,
    number_of_chapters: int,
    word_number: int,
    concept: Concept | dict | None = None,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    directive: str | None = None,
    progress=None,
) -> ArchitectureResult:
    """执行雪花四步,返回完整架构。纯生成,不落库。

    concept: 结构化故事概念(灵感工坊产出),优先于 topic 喂给核心种子;
             为空/None 时回落到 topic 一句话(向后兼容存量项目)。
    directive: 「架构研讨」对话得出的作者额外要求,高优先级注入四步;
               为空/None 时行为与旧版完全一致(向后兼容)。
    progress: 可选回调 fn(stage_text),四步各报一次(异步任务进度用)。
    """

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    adapter = get_adapter_for(Task.ARCHITECTURE)
    topic_block = _render_topic_block(topic, concept)
    directive_block = _render_directive_block(directive)

    # Step 1: 核心种子
    logger.info("架构生成 1/4:核心种子...")
    _report("1/4 核心种子")
    core_seed = (
        await adapter.ask(
            CORE_SEED_PROMPT.format(
                topic=topic_block,
                genre=genre,
                number_of_chapters=number_of_chapters,
                word_number=word_number,
                style_directives=style_block,
                directive_block=directive_block,
            )
        )
    ).strip()

    # Step 2: 角色动力学
    logger.info("架构生成 2/4:角色动力学...")
    _report("2/4 角色动力学")
    character_dynamics = (
        await adapter.ask(
            CHARACTER_DYNAMICS_PROMPT.format(
                core_seed=core_seed,
                style_directives=style_block,
                directive_block=directive_block,
            )
        )
    ).strip()

    # Step 3: 世界观
    logger.info("架构生成 3/4:世界观...")
    _report("3/4 世界观")
    world_building = (
        await adapter.ask(
            WORLD_BUILDING_PROMPT.format(
                core_seed=core_seed,
                character_dynamics=character_dynamics,
                style_directives=style_block,
                directive_block=directive_block,
            )
        )
    ).strip()

    # Step 4: 情节架构
    logger.info("架构生成 4/4:情节架构...")
    _report("4/4 情节架构")
    plot_architecture = (
        await adapter.ask(
            PLOT_ARCHITECTURE_PROMPT.format(
                core_seed=core_seed,
                character_dynamics=character_dynamics,
                world_building=world_building,
                number_of_chapters=number_of_chapters,
                style_directives=style_block,
                directive_block=directive_block,
            )
        )
    ).strip()

    logger.info("架构生成完成。")
    return ArchitectureResult(
        core_seed=core_seed,
        character_dynamics=character_dynamics,
        world_building=world_building,
        plot_architecture=plot_architecture,
    )


def save_architecture(
    db: Session, project: Project, result: ArchitectureResult
) -> Architecture:
    """架构落库:已有则覆盖并 version+1,否则新建。"""
    arch = project.architecture
    if arch is None:
        arch = Architecture(project_id=project.id, version=1)
        # 通过关系赋值,同时更新 session 里已缓存的 project.architecture
        project.architecture = arch
        db.add(arch)
    else:
        arch.version += 1

    arch.core_seed = result.core_seed
    arch.character_dynamics = result.character_dynamics
    arch.world_building = result.world_building
    arch.plot_architecture = result.plot_architecture

    project.status = "outlining"
    db.flush()
    return arch


# =============== 架构研讨(对话式,聊清楚不满意在哪 → 蒸馏成额外要求)===============
_MAX_ARCH_CHAT_TURNS = 40
_MAX_ARCH_MSG_LEN = 2000
_MAX_ARCH_BLOCK_CHARS = 4000  # 当前架构注入 system 时截断,防 token 膨胀


def _render_arch_block(arch: Architecture | None) -> str:
    """把当前架构四块渲染成注入研讨对话的上下文;无架构时给提示。"""
    if arch is None:
        return "(还没有生成过架构)"
    parts = [
        f"【核心种子】\n{arch.core_seed}",
        f"【角色动力学】\n{arch.character_dynamics}",
        f"【世界观】\n{arch.world_building}",
        f"【情节架构】\n{arch.plot_architecture}",
    ]
    text = "\n\n".join(p for p in parts if p.strip())
    return text[:_MAX_ARCH_BLOCK_CHARS] or "(架构为空)"


async def _arch_complete(adapter: LLMAdapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:带空回复重试 + 用量记账(对齐 ask 的兜底)。"""
    original_max = adapter.max_tokens
    try:
        for _ in range(3):
            resp = await adapter.complete(messages)
            adapter._record_usage(resp)
            content = (resp.content or "").strip()
            if content:
                return content
            adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
        raise RuntimeError("模型连续 3 次返回空回复")
    finally:
        adapter.max_tokens = original_max


def _format_arch_transcript(turns: list[dict], latest_reply: str) -> str:
    lines = [
        f"{'作者' if m['role'] == 'user' else '架构师'}:{(m['content'] or '').strip()}"
        for m in turns
    ]
    lines.append(f"架构师:{latest_reply}")
    return "\n".join(lines)


async def discuss_architecture(
    messages: list[dict],
    *,
    topic: str,
    concept: Concept | dict | None,
    arch: Architecture | None,
) -> dict:
    """就当前架构与作者多轮研讨:聊清楚不满意在哪,并蒸馏出「额外要求」。

    - messages:对话历史 [{role, content}, ...],最后一条应为作者(user)发言。
    - topic/concept/arch:上下文(本书概念、作者不满意的当前架构),仅供理解。

    返回 {reply, directive};directive 为蒸馏出的额外要求(可为空串),前端可
    带着它去重新生成架构。复用与 inspire chat 相同的「续聊 + 独立蒸馏」两段式。
    """
    turns = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-_MAX_ARCH_CHAT_TURNS:]
    if not turns:
        raise ValueError("请先说点什么")
    if turns[-1]["role"] != "user":
        raise ValueError("最后一条应为你的发言")

    topic_block = _render_topic_block(topic, concept)
    arch_block = _render_arch_block(arch)
    adapter = get_adapter_for(Task.ARCHITECTURE)

    # ① 续聊:system + 对话历史
    system = ARCH_CHAT_SYSTEM_PROMPT.format(topic_block=topic_block, arch_block=arch_block)
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m["role"], content=(m["content"] or "").strip()[:_MAX_ARCH_MSG_LEN])
        for m in turns
    ]
    reply = (await _arch_complete(adapter, chat_messages)).strip()
    if not reply:
        raise ValueError("模型没有回应,请重试")

    # ② 蒸馏:把含最新回复的完整对话提炼成「额外要求」(独立调用,不污染对话)
    transcript = _format_arch_transcript(turns, reply)
    directive = ""
    try:
        raw = (await adapter.ask(ARCH_DISTILL_PROMPT.format(transcript=transcript))).strip()
        # 蒸馏出"尚无明确意见"时约定回一个短横线,归一化成空串
        if raw and raw != "-":
            directive = raw
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("架构研讨蒸馏失败,directive 置空", exc_info=True)

    return {"reply": reply, "directive": directive}
