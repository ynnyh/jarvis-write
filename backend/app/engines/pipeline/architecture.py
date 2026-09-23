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

from app.db.models import Architecture, Chapter, Outline, Project
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.base import LLMAdapter, LLMMessage, complete_text_with_budget
from app.llm.router import Task, get_adapter_for
from app.prompts import (
    ARCH_CHAT_SYSTEM_PROMPT,
    ARCH_DISTILL_PROMPT,
    CHARACTER_DYNAMICS_PROMPT,
    CORE_SEED_PROMPT,
    PLOT_ARCHITECTURE_OPEN_PROMPT,
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


# =============== 架构闸门(docs/确认链 L2):逐层生成/拍板/作废 ===============
# 雪花四层各自成为一个人工确认点:第 N 层只吃 1..N-1 层**已拍板**的产物,
# 上游重出/手改 → 该层与下游全部回未拍板(拍板永远对着看过、认过的那版)。
ARCH_LAYERS: tuple[tuple[str, str], ...] = (
    ("core_seed", "核心种子"),
    ("character_dynamics", "角色动力学"),
    ("world_building", "世界观"),
    ("plot_architecture", "情节架构"),
)
LAYER_LABEL: dict[str, str] = dict(ARCH_LAYERS)
LAYER_KEYS: tuple[str, ...] = tuple(k for k, _ in ARCH_LAYERS)


def validate_layer(layer: str) -> None:
    """层 key 合法性;非法抛 ValueError(端点层转 400)。"""
    if layer not in LAYER_LABEL:
        raise ValueError(f"未知架构层:{layer}")


def _require_upstream(value: str, label: str) -> None:
    if not (value or "").strip():
        raise ValueError(f"上游「{label}」尚未生成,请先生成并拍板上游")


def layer_state(arch: Architecture | None) -> dict[str, bool]:
    """读逐层拍板态。

    NULL(存量架构,未拆层时代生成)= 全部已拍板:旧行为零变化,旧书回访
    架构工作墙时四层直接视为已认,仍可逐层撤回重出。无架构 = 全层未拍板。
    """
    if arch is None:
        return {k: False for k in LAYER_KEYS}
    saved = arch.confirmed_layers or {}
    return {k: bool(saved.get(k, True)) for k in LAYER_KEYS}


def _set_layer_state(arch: Architecture, state: dict[str, bool]) -> None:
    arch.confirmed_layers = {k: bool(state.get(k, False)) for k in LAYER_KEYS}


async def generate_architecture_layer(
    *,
    layer: str,
    topic: str,
    genre: str,
    number_of_chapters: int,
    word_number: int,
    concept: Concept | dict | None = None,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    directive: str | None = None,
    dna: object | None = None,
    open_ended: bool = False,
    core_seed: str = "",
    character_dynamics: str = "",
    world_building: str = "",
) -> str:
    """生成架构的指定一层,返回该层文本(架构闸门逐层生成/带话重出用)。

    与全书串行的 generate_architecture 共用同一组 prompt——单一代码路径,
    整本串行只是逐层调本函数的便捷封装。上游产物经参数显式传入:闸门模式
    只传**已拍板**的上游;依赖的上游为空时抛 ValueError(调用顺序错误)。
    """
    validate_layer(layer)
    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    if dna is not None:
        # 故事 DNA(味道锚+故事骨架):未设置时返回空串,prompt 一字不变
        from app.engines.tendency.assembler import dna_block_of

        style_block += dna_block_of(dna)
    adapter = get_adapter_for(Task.ARCHITECTURE)
    topic_block = _render_topic_block(topic, concept)
    directive_block = _render_directive_block(directive)
    # 字数盘子:只有核心种子填了每章字数,情节架构/章节蓝图此前完全不知道字数,
    # 导致大纲按戏剧冲突自然铺、总规模放飞。这里算总盘子注入情节架构(Step4)。
    word_scope = (
        f"，总篇幅约 {number_of_chapters * word_number} 字（每章约 {word_number} 字）"
        if word_number
        else ""
    )

    if layer == "core_seed":
        return (
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
    if layer == "character_dynamics":
        _require_upstream(core_seed, "核心种子")
        return (
            await adapter.ask(
                CHARACTER_DYNAMICS_PROMPT.format(
                    core_seed=core_seed,
                    style_directives=style_block,
                    directive_block=directive_block,
                )
            )
        ).strip()
    if layer == "world_building":
        _require_upstream(core_seed, "核心种子")
        _require_upstream(character_dynamics, "角色动力学")
        return (
            await adapter.ask(
                WORLD_BUILDING_PROMPT.format(
                    core_seed=core_seed,
                    character_dynamics=character_dynamics,
                    style_directives=style_block,
                    directive_block=directive_block,
                )
            )
        ).strip()
    # layer == "plot_architecture"
    _require_upstream(core_seed, "核心种子")
    _require_upstream(character_dynamics, "角色动力学")
    _require_upstream(world_building, "世界观")
    plot_prompt = PLOT_ARCHITECTURE_OPEN_PROMPT if open_ended else PLOT_ARCHITECTURE_PROMPT
    return (
        await adapter.ask(
            plot_prompt.format(
                core_seed=core_seed,
                character_dynamics=character_dynamics,
                world_building=world_building,
                number_of_chapters=number_of_chapters,
                word_scope=word_scope,
                style_directives=style_block,
                directive_block=directive_block,
            )
        )
    ).strip()


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
    dna: object | None = None,
    open_ended: bool = False,
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

    common = dict(
        topic=topic,
        genre=genre,
        number_of_chapters=number_of_chapters,
        word_number=word_number,
        concept=concept,
        tendency=tendency,
        global_tendency=global_tendency,
        directive=directive,
        dna=dna,
        open_ended=open_ended,
    )

    # 四步逐层生成(单一代码路径:与架构闸门的逐层端点共用 generate_architecture_layer)
    logger.info("架构生成 1/4:核心种子...")
    _report("1/4 核心种子")
    core_seed = await generate_architecture_layer(layer="core_seed", **common)

    logger.info("架构生成 2/4:角色动力学...")
    _report("2/4 角色动力学")
    character_dynamics = await generate_architecture_layer(
        layer="character_dynamics", core_seed=core_seed, **common
    )

    logger.info("架构生成 3/4:世界观...")
    _report("3/4 世界观")
    world_building = await generate_architecture_layer(
        layer="world_building", core_seed=core_seed,
        character_dynamics=character_dynamics, **common
    )

    logger.info("架构生成 4/4:情节架构(open_ended=%s)...", open_ended)
    _report("4/4 情节架构")
    plot_architecture = await generate_architecture_layer(
        layer="plot_architecture", core_seed=core_seed,
        character_dynamics=character_dynamics, world_building=world_building, **common
    )

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
    prev_text = ""
    if arch is None:
        arch = Architecture(project_id=project.id, version=1)
        # 通过关系赋值,同时更新 session 里已缓存的 project.architecture
        project.architecture = arch
        db.add(arch)
    else:
        arch.version += 1
        prev_text = "\n".join(
            (arch.core_seed, arch.character_dynamics, arch.world_building, arch.plot_architecture)
        )

    arch.core_seed = result.core_seed
    arch.character_dynamics = result.character_dynamics
    arch.world_building = result.world_building
    arch.plot_architecture = result.plot_architecture
    # 已按（可能刚换过的）最新概念重生成，消退「基于旧概念」标记
    arch.concept_stale = False
    # 整本重出走的是信任模式链路(不设闸):拍板态回 NULL=全层已认,
    # 旧书回访工作墙时仍可逐层撤回重出
    arch.confirmed_layers = None

    # 架构真正重写(内容变了)且已有大纲 → 整组大纲落到旧架构上:标 outline_stale,
    # 并把已写正文标失配,前端大纲页据此让作者选「重铺/清空」。首次建架构无大纲则跳过。
    if arch.version > 1:
        new_text = "\n".join(
            (
                arch.core_seed,
                arch.character_dynamics,
                arch.world_building,
                arch.plot_architecture,
            )
        )
        old_text = prev_text
        if new_text.strip() != (old_text or "").strip() and _has_outlines(db, project.id):
            project.outline_stale = True
            _mark_written_chapters_stale(db, project.id)

    project.status = "outlining"
    db.flush()
    return arch


def save_architecture_layer(
    db: Session, project: Project, layer: str, text: str
) -> Architecture:
    """架构闸门单层产物落库(逐层生成/带话重出共用)。

    - 无架构则建行(全层未拍板);该层文本覆盖,内容真变才 version+1;
    - 该层与下游全部回未拍板——雪花依赖链上,上游变了下游就不再被认;
    - 已拍板过的层内容真变且已有大纲 → outline_stale + 已写章失配
      (与整本重出 save_architecture 同语义:整组大纲落在旧架构上)。
    """
    validate_layer(layer)
    arch = project.architecture
    created = False
    if arch is None:
        arch = Architecture(project_id=project.id, version=1)
        project.architecture = arch
        db.add(arch)
        created = True
    prev_state = layer_state(arch)
    prev_text = (getattr(arch, layer) or "") if not created else ""
    changed = prev_text.strip() != (text or "").strip()
    if changed:
        arch.version += 1
    setattr(arch, layer, text)
    state = prev_state.copy()
    idx = LAYER_KEYS.index(layer)
    for k in LAYER_KEYS[idx:]:
        state[k] = False
    _set_layer_state(arch, state)
    if (
        not created
        and prev_state.get(layer)
        and changed
        and prev_text.strip()
        and _has_outlines(db, project.id)
    ):
        project.outline_stale = True
        _mark_written_chapters_stale(db, project.id)
    project.status = "outlining"
    db.flush()
    return arch


def confirm_architecture_layer(
    db: Session, project: Project, layer: str, confirmed: bool
) -> Architecture:
    """拍板/撤回架构的指定一层。

    拍板的链式约束:上游必须全部已拍板(拍板语义=基于已拍板的上游往下认);
    撤回的级联:该层撤回 → 下游一并回未拍板(下游基于的上游已不被认)。
    层还没内容时拍板无意义,抛 ValueError。违反约束/无架构同样 ValueError。
    """
    validate_layer(layer)
    arch = project.architecture
    if arch is None:
        raise ValueError("尚未生成架构")
    if not (getattr(arch, layer) or "").strip():
        raise ValueError(f"「{LAYER_LABEL[layer]}」还没有内容,先生成或手写再拍板")
    state = layer_state(arch)
    idx = LAYER_KEYS.index(layer)
    if confirmed:
        missing = [
            LAYER_LABEL[k] for k in LAYER_KEYS[:idx] if not state.get(k)
        ]
        if missing:
            raise ValueError(
                f"上游 {'、'.join(missing)} 尚未拍板,先拍板上游再拍这层"
            )
    state[layer] = confirmed
    if not confirmed:
        for k in LAYER_KEYS[idx + 1:]:
            state[k] = False
    _set_layer_state(arch, state)
    db.flush()
    return arch


def mark_layers_edited(arch: Architecture, fields: list[str]) -> None:
    """手动编辑层 → 该层与下游回未拍板(拍板永远对着看过、认过的那版)。"""
    state = layer_state(arch)
    touched = False
    for field in fields:
        if field in LAYER_KEYS:
            idx = LAYER_KEYS.index(field)
            for k in LAYER_KEYS[idx:]:
                state[k] = False
            touched = True
    if touched:
        _set_layer_state(arch, state)


def _has_outlines(db: Session, project_id: int) -> bool:
    return (
        db.query(Outline.id)
        .filter(Outline.project_id == project_id)
        .first()
        is not None
    )


def _mark_written_chapters_stale(db: Session, project_id: int) -> None:
    """架构换了,旧正文本体对不上了:把所有已写章(有 final_content)标失配。"""
    db.query(Chapter).filter(
        Chapter.project_id == project_id, Chapter.final_content != ""
    ).update(
        {Chapter.is_stale: True, Chapter.status: "stale"},
        synchronize_session=False,
    )


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
    """多轮 complete 的薄封装:空正文放大预算重试 + 用量记账。

    别在这里自己写"空串就翻倍"的循环:complete() 遇空正文是抛 EmptyContentError,
    统一交给 complete_text_with_budget 处理(见 llm/base.py)。
    """
    return await complete_text_with_budget(adapter, messages)


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
