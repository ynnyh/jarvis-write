# app/api/inspire.py
# -*- coding: utf-8 -*-
"""灵感接口:碎片/想法 → 结构化故事概念。独立于项目,可在建项目前用。

三条路(见 docs 灵感工坊设计):
  POST /api/inspire         出方案:碎片 → N 个差异化结构化概念
  POST /api/inspire/refine  指令式:当前概念 + 一句话修改 → 改后概念(带 diff)
  POST /api/inspire/chat    对话式:多轮聊 → 每轮沉淀出结构化概念草稿

概念结构统一走 app/schemas/concept.py(六字段,LLM 幻觉字段一律丢弃)。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user_id, get_current_user
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drift import (
    confirm_forbidden,
    effective_patterns,
    forbidden_for_mode,
    forbidden_labels_for_mode,
    genre_fit_report,
)
from app.engines.drift.contracts import _opted_in
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import dna_block_of, render_style_block
from app.jobs import spawn_job
from app.llm.base import LLMAdapter, LLMMessage, complete_text_with_budget
from app.llm.router import Task, get_adapter_for
from app.prompts.dna_capsules import dna_capsule_choices, get_dna_capsule
from app.prompts.inspire import (
    CHAT_DISTILL_PROMPT,
    CHAT_SYSTEM_PROMPT,
    INSPIRE_PROMPT,
    MIRROR_DISTILL_PROMPT,
    REFINE_PROMPT,
    _GENRE_BOUNDARY,
)
from app.schemas.concept import CONCEPT_FIELDS, Concept, coerce_concept
from app.schemas.dna import DNA_MODES, TASTE_AXES, StoryDNA, coerce_dna
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.inspire")

_MODE_LABELS = dict(DNA_MODES)  # {key: 中文标签},供矛盾检测复述模式名

router = APIRouter(
    prefix="/api/inspire",
    tags=["inspire"],
    dependencies=[Depends(get_current_user)],
)

# 对话轮数上限:防止 transcript 无限膨胀吃 token / 拖慢蒸馏
_MAX_CHAT_TURNS = 40
_MAX_MSG_LEN = 2000


# ============================= 出方案 =============================
class InspireRequest(BaseModel):
    spark: str = Field(default="", description="灵感碎片,可为空")
    tendency: Tendency = Field(default_factory=dict)
    count: int = Field(default=4, ge=2, le=6)
    # 故事 DNA / 本书基因(坐标卡产出):注入生成 + 驱动题材硬门
    dna: StoryDNA | None = None


class InspireResponse(BaseModel):
    ideas: list[Concept]
    comparison: str = Field(default="", description="几个概念的差异化对比与目标读者说明")


def _dna_of(dna: StoryDNA | None) -> StoryDNA:
    """请求里的 DNA 收敛成合法 StoryDNA(None → 空;顺带清洗非法 mode/axes)。"""
    return coerce_dna(dna.model_dump()) if dna is not None else StoryDNA()


async def _generate_ideas(
    req: InspireRequest, dna_block: str, count: int, avoid_labels: tuple[str, ...] = ()
) -> tuple[list[Concept], str]:
    """生成一批概念:味道锚追加进 style_block(不占模板占位符);重生时叠加「本轮绝不能有」。

    返回 (概念列表, comparison 文案)。ask 失败抛 502(首轮致命;重生轮由调用方兜底)。
    """
    style_block = render_style_block(assemble_tendency("outline", req.tendency))
    style_block += dna_block
    if avoid_labels:
        style_block += (
            "\n【上一轮生成跑偏了,本轮绝对不能再出现以下套路元素】:"
            + "、".join(sorted(avoid_labels))
            + "\n"
        )
    prompt = INSPIRE_PROMPT.format(
        spark=req.spark.strip() or "(空白,自由发挥)",
        count=count,
        style_directives=style_block,
        genre_boundary=_GENRE_BOUNDARY,
    )
    raw = await get_adapter_for(Task.ARCHITECTURE).ask(prompt)
    data = parse_llm_json(raw)
    ideas = [
        coerce_concept(i) for i in (data.get("ideas") or []) if isinstance(i, dict)
    ]
    return ideas, str(data.get("comparison") or "").strip()


async def _screen_concepts(
    ideas: list[Concept], story: StoryDNA
) -> tuple[list[Concept], set[str]]:
    """题材硬门筛查:regex 高召回预筛 → LLM 精筛去误报 → 丢弃确认越界的概念。

    返回 (保留的干净概念, 本轮确认为真的越界元素名集合)。用户从不看到被丢弃的跑偏概念。
    """
    must, must_not, mode = tuple(story.must), tuple(story.must_not), story.mode
    kept: list[Concept] = []
    flagged: list[tuple[Concept, object]] = []
    for c in ideas:
        rep = genre_fit_report(c.render(), mode=mode, must=must, must_not=must_not)
        if rep.has_forbidden():
            flagged.append((c, rep))
        else:
            kept.append(c)
    if not flagged:
        return kept, set()

    # 每个疑似概念并发精筛(FAST 档;通常只有 0-2 个进到这里)
    confirms = await asyncio.gather(
        *[
            confirm_forbidden(c.render(), rep.forbidden_labels(), mode, rep.forbidden)
            for c, rep in flagged
        ]
    )
    killed: set[str] = set()
    for (c, _rep), conf in zip(flagged, confirms):
        if conf:
            killed.update(conf)  # 确认越界 → 丢弃
        else:
            kept.append(c)       # 误报 → 放行
    return kept, killed


async def _gate_and_refill(
    req: InspireRequest,
    story: StoryDNA,
    dna_block: str,
    ideas: list[Concept],
    max_regens: int = 2,
) -> tuple[list[Concept], set[str]]:
    """负向硬门主循环:筛掉跑偏概念,不足则强化重生补齐,限轮兜底。

    返回 (最终概念列表, 累计被毙的越界元素名)。重生阶段的 LLM 抖动不致命(已有干净概念时降级保留)。
    """
    kept, killed = await _screen_concepts(ideas, story)
    avoid = set(killed)
    regens = 0
    while len(kept) < req.count and regens < max_regens and avoid:
        regens += 1
        need = req.count - len(kept)
        try:
            fresh, _ = await _generate_ideas(
                req, dna_block, min(6, max(2, need + 1)), tuple(avoid)
            )
        except Exception:  # noqa: BLE001 — 重生抖动不该毁掉已筛出的干净概念
            logger.warning("题材硬门重生调用失败,保留已有干净概念", exc_info=True)
            break
        fresh_kept, fresh_killed = await _screen_concepts(fresh, story)
        avoid.update(fresh_killed)
        seen = {c.logline.strip() for c in kept}
        for c in fresh_kept:
            key = c.logline.strip()
            if c.is_empty() or key in seen:
                continue
            kept.append(c)
            seen.add(key)
            if len(kept) >= req.count:
                break
    if killed or regens:
        logger.info(
            "题材硬门:毙 %s,重生 %d 轮,最终留 %d 个概念",
            "、".join(sorted(killed)) or "-", regens, len(kept),
        )
    return kept, killed


async def _inspire_impl(req: InspireRequest) -> InspireResponse:
    story = _dna_of(req.dna)
    dna_block = dna_block_of(story)
    try:
        ideas, comparison = await _generate_ideas(req, dna_block, req.count)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"灵感生成失败: {exc}") from exc

    # 题材硬门:仅当该 DNA 确有生效的禁忌集(现实向,或用户自定义 must_not)才启用
    killed: set[str] = set()
    gate_on = bool(effective_patterns(story.mode, tuple(story.must_not), tuple(story.must)))
    if gate_on:
        ideas, killed = await _gate_and_refill(req, story, dna_block, ideas)

    # 丢弃全空概念(解析残缺);全军覆没才报错
    ideas = [c for c in ideas if not c.is_empty()][: req.count]
    if not ideas:
        detail = (
            "生成的概念都与你设定的题材不符,已被拦下,请重试或放宽坐标设定"
            if killed else "灵感解析失败,请重试"
        )
        raise HTTPException(status_code=502, detail=detail)
    return InspireResponse(ideas=ideas, comparison=comparison)


@router.post("", response_model=InspireResponse)
async def inspire(req: InspireRequest) -> InspireResponse:
    """从灵感碎片扩展出 N 个结构化故事概念(强模型,约 1-2 分钟)。"""
    return await _inspire_impl(req)


@router.post("/async")
async def inspire_async(req: InspireRequest):
    """异步版出方案:立即返回 job_id,前端轮询/任务中心接管。"""
    uid = current_user_id.get()

    async def work(progress):
        progress("AI 正在扩展故事概念")
        return (await _inspire_impl(req)).model_dump()

    return {"job_id": spawn_job(f"inspire-u{uid}", work)}


# ============================= 指令式改 =============================
class RefineRequest(BaseModel):
    concept: Concept
    directive: str = Field(min_length=1, description="一句话修改意见")
    tendency: Tendency = Field(default_factory=dict)
    dna: StoryDNA | None = None


class RefineResponse(BaseModel):
    concept: Concept
    changed: list[str] = Field(default_factory=list, description="实际改动的字段名")
    note: str = ""


@router.post("/refine", response_model=RefineResponse)
async def refine(req: RefineRequest) -> RefineResponse:
    """指令式局部改:据修改意见改写当前概念,前端做字段级 diff 预览后落库。"""
    return await _refine_impl(req)


@router.post("/refine-async")
async def refine_async(req: RefineRequest):
    """异步版指令式改:立即返回 job_id。"""
    uid = current_user_id.get()

    async def work(progress):
        progress("AI 正在按你的意见改写概念")
        return (await _refine_impl(req)).model_dump()

    return {"job_id": spawn_job(f"inspire-refine-u{uid}", work)}


async def _refine_impl(req: RefineRequest) -> RefineResponse:
    directive = req.directive.strip()
    if not directive:
        raise HTTPException(status_code=400, detail="修改意见不能为空")
    if len(directive) > 500:
        raise HTTPException(status_code=400, detail="修改意见过长(限 500 字)")
    if req.concept.is_empty():
        raise HTTPException(status_code=400, detail="当前还没有概念可改,请先生成或填写")

    # 味道锚(本书基因)折进 genre_boundary 自由文本槽注入(REFINE_PROMPT 无 style 占位符)
    dna_block = dna_block_of(_dna_of(req.dna))
    prompt = REFINE_PROMPT.format(
        concept_block=req.concept.render(),
        directive=directive,
        genre_boundary=_GENRE_BOUNDARY + (("\n" + dna_block) if dna_block else ""),
    )
    try:
        raw = await get_adapter_for(Task.ARCHITECTURE).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"概念改写失败: {exc}") from exc

    data = parse_llm_json(raw)
    new_concept = coerce_concept(data.get("concept"))
    if new_concept.is_empty():
        raise HTTPException(status_code=502, detail="概念改写解析失败,请重试")

    # changed 以后端为准重算(不轻信模型自报),与原概念逐字段比对
    valid_fields = {k for k, _ in CONCEPT_FIELDS}
    changed = [
        k for k, _ in CONCEPT_FIELDS
        if getattr(new_concept, k).strip() != getattr(req.concept, k).strip()
    ]
    # 模型自报的 changed 仅作补充(它可能语义上"改了"但文字近似),取并集且过滤非法字段
    for k in data.get("changed") or []:
        if isinstance(k, str) and k in valid_fields and k not in changed:
            changed.append(k)

    return RefineResponse(
        concept=new_concept,
        changed=changed,
        note=str(data.get("note") or "").strip(),
    )


# ============================= 对话式捏 =============================
class ChatMessage(BaseModel):
    role: str = Field(description="user / assistant")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    concept: Concept | None = None
    tendency: Tendency = Field(default_factory=dict)
    dna: StoryDNA | None = None


class ChatResponse(BaseModel):
    reply: str
    concept: Concept


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """对话式构思:回一句引导,并把整段对话蒸馏成结构化概念草稿。

    两次 LLM 调用:①按对话历史续聊 ②独立蒸馏成概念(不污染对话上下文)。
    """
    # 归一化 + 防御:限轮数、限单条长度、只留合法角色
    turns = [
        m for m in req.messages
        if m.role in ("user", "assistant") and m.content.strip()
    ][-_MAX_CHAT_TURNS:]
    if not turns:
        raise HTTPException(status_code=400, detail="请先说点什么")
    if turns[-1].role != "user":
        raise HTTPException(status_code=400, detail="最后一条应为用户发言")

    assembled = assemble_tendency("outline", req.tendency)
    style_block = render_style_block(assembled)
    style_block += dna_block_of(_dna_of(req.dna))  # 味道锚(本书基因)注入对话引导
    current = req.concept or Concept()
    adapter = get_adapter_for(Task.ARCHITECTURE)

    # ① 续聊:system + 对话历史
    system = CHAT_SYSTEM_PROMPT.format(
        style_directives=style_block,
        concept_block=current.render() or "(还没有,刚开始聊)",
    )
    chat_messages = [LLMMessage(role="system", content=system)] + [
        LLMMessage(role=m.role, content=m.content.strip()[:_MAX_MSG_LEN])
        for m in turns
    ]
    try:
        reply = (await _complete_text(adapter, chat_messages)).strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"对话失败: {exc}") from exc
    if not reply:
        raise HTTPException(status_code=502, detail="模型没有回应,请重试")

    # ② 蒸馏:把含最新 AI 回复的完整对话提炼成概念(独立调用)
    transcript = _format_transcript(turns, reply)
    distilled = current  # 蒸馏失败时回落到既有概念,不丢用户已捏出的东西
    try:
        raw = await adapter.ask(
            CHAT_DISTILL_PROMPT.format(transcript=transcript, genre_boundary=_GENRE_BOUNDARY)
        )
        candidate = coerce_concept(parse_llm_json(raw))
        if not candidate.is_empty():
            distilled = candidate
    except Exception:  # noqa: BLE001 — 蒸馏失败不阻塞对话
        logger.warning("对话概念蒸馏失败,沿用既有概念", exc_info=True)

    return ChatResponse(reply=reply, concept=distilled)


async def _complete_text(adapter: LLMAdapter, messages: list[LLMMessage]) -> str:
    """多轮 complete 的薄封装:空正文放大预算重试 + 用量记账。

    别在这里自己写"空串就翻倍"的循环:complete() 遇空正文是抛 EmptyContentError,
    统一交给 complete_text_with_budget 处理(见 llm/base.py)。
    """
    return await complete_text_with_budget(adapter, messages)


def _format_transcript(turns: list[ChatMessage], latest_reply: str) -> str:
    lines = [
        f"{'作者' if m.role == 'user' else '策划'}:{m.content.strip()}"
        for m in turns
    ]
    lines.append(f"策划:{latest_reply}")
    return "\n".join(lines)


# ============================= 坐标卡 & 品味镜 =============================
@router.get("/dna/options")
async def dna_options() -> dict:
    """坐标卡的静态选项:味道锚胶囊 / 题材模式 / 味道轴 / 各模式的禁忌套路清单。

    前端据此渲染坐标卡下拉与滑杆;forbidden_by_mode 让前端在选「现实向」时
    即时展示「会拦哪些套路」,与后端硬门口径一致。
    """
    return {
        "capsules": dna_capsule_choices(),
        "modes": [{"key": k, "label": label} for k, label in DNA_MODES],
        "axes": [
            {"key": k, "label": label, "left": lo, "right": hi}
            for k, label, lo, hi in TASTE_AXES
        ],
        "forbidden_by_mode": {k: forbidden_labels_for_mode(k) for k, _ in DNA_MODES},
    }


class MirrorRequest(BaseModel):
    dna: StoryDNA
    spark: str = Field(default="", description="灵感碎片(可选,仅供蒸馏参考)")


class MirrorResponse(BaseModel):
    basis: str = Field(default="", description="结构化本书基因(StoryDNA.render)")
    reflection: str = Field(default="", description="一段人话复述『这本书要写的味道』")
    contradictions: list[str] = Field(
        default_factory=list, description="坐标自相矛盾/需确认的地方"
    )
    forbidden: list[str] = Field(
        default_factory=list, description="该模式生效的禁忌套路(已减去明确要的)"
    )


def _dna_contradictions(story: StoryDNA) -> list[str]:
    """纯规则检测坐标卡里的自相矛盾/需要作者确认的地方(零 LLM,快而稳)。"""
    out: list[str] = []
    must = {m.strip() for m in story.must if m.strip()}

    # 1) 同一元素同时进了「必须有」与「绝不能有」
    for m in story.must_not:
        if m.strip() and m.strip() in must:
            out.append(f"『{m.strip()}』同时被列进「必须有」和「绝不能有」,自相矛盾")

    # 2) 选的味道锚胶囊模式,与题材模式不一致
    cap = get_dna_capsule(story.taste_key)
    if cap and story.mode and cap.mode and cap.mode != story.mode:
        out.append(
            f"选的味道锚「{cap.name}」偏{_MODE_LABELS.get(cap.mode, cap.mode)},"
            f"但题材模式设成了{story.mode_label()},两者不一致"
        )

    # 3) 笔触轴与题材模式互相拉扯
    realism = (story.axes.get("realism") or "").strip()
    if story.mode == "realistic" and realism == "梦幻":
        out.append("题材模式是现实向,但笔触轴选了『梦幻』,可能互相拉扯")
    if story.mode == "fantasy" and realism == "写实":
        out.append("题材模式是幻想向,但笔触轴选了『写实』,确认是要低幻想质感?")

    # 4) 硬门模式里把套路元素列进了「必须有」(等于为它开一道口子)
    if story.mode:
        opt_in = tuple(must)
        for e in forbidden_for_mode(story.mode):
            if _opted_in(e.label, opt_in):
                out.append(
                    f"{story.mode_label()}硬门里,你把套路元素『{e.label}』列进了「必须有」"
                    "——这会为它放行,确认要保留?"
                )
    return out


def _mirror_input(story: StoryDNA) -> str:
    """喂给品味镜蒸馏的干净输入:结构化基因 + 胶囊味道取向(不含配对反例/禁忌清单)。"""
    parts = [story.render()]
    cap = get_dna_capsule(story.taste_key)
    if cap:
        parts.append(f"味道取向(参照「{cap.name}」):{cap.directive}")
    return "\n".join(p for p in parts if p.strip())


@router.post("/dna/mirror", response_model=MirrorResponse)
async def dna_mirror(req: MirrorRequest) -> MirrorResponse:
    """品味镜:生成前把坐标卡蒸馏成一段人话,连同矛盾检测一起返回,先照镜子再烧 token。

    reflection 走 FAST 档蒸馏,失败回落到结构化基因(basis);contradictions/forbidden
    纯规则算出,零成本、恒定可用。
    """
    story = coerce_dna(req.dna.model_dump())
    basis = story.render()
    contradictions = _dna_contradictions(story)
    opt_in = tuple(m.strip() for m in story.must if m.strip())
    forbidden = [
        e.label for e in forbidden_for_mode(story.mode) if not _opted_in(e.label, opt_in)
    ]

    reflection = ""
    mirror_src = _mirror_input(story)
    if mirror_src.strip():
        try:
            reflection = (
                await get_adapter_for(Task.SUMMARY).ask(
                    MIRROR_DISTILL_PROMPT.format(dna_block=mirror_src)
                )
            ).strip()
        except Exception:  # noqa: BLE001 — 蒸馏失败回落到结构化基因,不阻塞
            logger.warning("品味镜蒸馏失败,回落到结构化基因", exc_info=True)

    return MirrorResponse(
        basis=basis,
        reflection=reflection or basis,
        contradictions=contradictions,
        forbidden=forbidden,
    )
