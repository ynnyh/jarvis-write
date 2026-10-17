# app/api/projects/intent.py
# -*- coding: utf-8 -*-
"""开书屏 2「理解」:理解卡回读 + 确认①(docs/30 / docs/32 §2.1)。

两处确认里的第一处。此前这条路上有五道签字(mode → idea → brief → concept →
setup → launch),每道都要求作者在**还没看到任何正文**时点头,而后端还在其中偷偷
用一次 13 token 的调用把题材定死(真实历史 → 架空历史,传国玉玺案例的病根)。

现在:AI 先把「我听到的」复述成一张卡(世界坐标 / 叙述视角 / 主角与关系 / 味道 /
必须保留 / 不要出现 / 题材),作者改到对味,点一次「✓ 就按这个来,先写三章给我看」。
题材以卡里的显式值为准落库,不再由推断静默覆盖。

端点:
  POST /api/projects/{pid}/intent-readback  回读「我听到的」→ 理解卡草稿(**不落库**)
  POST /api/projects/{pid}/intent-confirm   确认①:落卡 + 渲染开书订单 + 启动样稿链
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record_gen_inputs
from app.db.models import Project
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.engines.intent import clean_intent_card, intent_card_empty
from app.engines.skills.packs import render_project_skill_block
from app.llm.router import Task, get_adapter_for
from app.prompts.intent import INTENT_READBACK_PROMPT
from app.schemas.project import ProjectOut

from ._common import _get_project_or_404
from .chain import spawn_sample_chain
from .plans import (
    _SERIAL_SCALE_MAP,
    _SHORT_SCALE_MAP,
    _TITLE_SENTINEL,
    _render_order,
)

logger = logging.getLogger("jarvis-write.intent")

router = APIRouter()

_TOPIC_MAX = 2000
_FEEDBACK_MAX = 500


class IntentCard(BaseModel):
    """理解卡:全链生成的最高约束。字段与 app.engines.intent.INTENT_FIELDS 对齐。"""

    world: str = ""
    viewpoint: str = ""
    cast: str = ""
    flavor: list[str] = Field(default_factory=list)
    must: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    genre: str = ""
    # 拿不准的字段 + 候选(前端据此在对应字段下出候选 chip)
    uncertain: list[dict] = Field(default_factory=list)


class IntentReadbackRequest(BaseModel):
    mode: str = Field(default="serial", max_length=10)
    topic: str = Field(default="", max_length=_TOPIC_MAX)
    genre: str = Field(default="", max_length=100)
    # 作者对上一条理解的补充(「不是架空,就是真实历史」):重出时带上
    feedback: str = Field(default="", max_length=_FEEDBACK_MAX)
    avoid: list[str] = Field(default_factory=list)


class IntentReadbackResponse(BaseModel):
    card: IntentCard
    project: ProjectOut


class IntentConfirmRequest(BaseModel):
    card: IntentCard
    mode: str = Field(default="serial", max_length=10)
    # 选中的方向卡(可空:作者可能不改方向,直接按理解卡出样稿)
    index: int | None = Field(default=None, ge=0, le=9)
    # 屏 0 手选的篇幅档,优先于方向卡自带推荐档
    scale_override: dict[str, int] | None = None


class IntentConfirmResponse(BaseModel):
    project: ProjectOut
    sample_job_id: str


@router.post("/{project_id}/intent-readback", response_model=IntentReadbackResponse)
async def intent_readback(
    project_id: int, req: IntentReadbackRequest, db: Session = Depends(get_db)
) -> IntentReadbackResponse:
    """回读「我听到的」→ 理解卡草稿。**不落库**(草稿),作者确认才进 intent-confirm。

    用强档模型(Task.ARCHITECTURE)而不是 FAST:这一次调用决定整本书的世界坐标,
    传国玉玺案例就是被一次廉价推断带偏的——这里省下的钱,后面要用整本书的返工还。
    """
    project = _get_project_or_404(db, project_id)
    topic = req.topic.strip() or (project.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="先写一句你的点子(哪怕一句话),再让 AI 回读")

    feedback_block = ""
    if req.feedback.strip():
        feedback_block = f"\n【作者对上一条理解的补充(务必落实)】\n{req.feedback.strip()}\n"
    avoid_block = ""
    if req.avoid:
        cleaned = [str(a).strip()[:60] for a in req.avoid[:8] if str(a).strip()]
        if cleaned:
            avoid_block = "\n【作者已明确不要的方向(一律不得出现)】\n- " + "\n- ".join(cleaned) + "\n"

    prompt = INTENT_READBACK_PROMPT.format(
        topic=topic,
        genre=req.genre.strip() or project.genre or "(未定)",
        feedback_block=feedback_block,
        avoid_block=avoid_block,
        skill_block=render_project_skill_block(db, project, "idea"),
    )
    adapter = get_adapter_for(Task.ARCHITECTURE)
    try:
        data = parse_llm_json(await adapter.ask(prompt))
    except Exception as exc:  # noqa: BLE001 — 失败原因直接反馈给作者
        raise HTTPException(status_code=502, detail=f"回读失败:{exc}") from exc
    card = clean_intent_card(data)
    if intent_card_empty(card):
        raise HTTPException(status_code=502, detail="回读失败:模型没给出可用的理解卡,再试一次")

    record_gen_inputs(
        kind="intent-readback", project_id=project_id,
        payload={
            "topic": topic, "genre": req.genre, "mode": req.mode,
            "feedback": req.feedback, "avoid": req.avoid,
        },
        db=db,
    )
    db.commit()
    return IntentReadbackResponse(
        card=IntentCard(**card),
        project=ProjectOut.model_validate(project, from_attributes=True),
    )


@router.post("/{project_id}/intent-confirm", response_model=IntentConfirmResponse)
async def intent_confirm(
    project_id: int, req: IntentConfirmRequest, db: Session = Depends(get_db)
) -> IntentConfirmResponse:
    """确认①:理解卡 + 选中的方向卡 → 落库(卡/题材/开书订单/篇幅)→ 启动样稿链。

    落库后下游全链都从这张卡派生:概念深化、架构四层、骨架、蓝图、正文,每处都拿
    卡里的世界坐标与主角当硬约束(app.engines.intent.render_intent_block)。
    产物层(概念/架构/骨架/大纲)不再是闸门——它们在样稿屏与工作台里可看可改。
    """
    project = _get_project_or_404(db, project_id)
    card = clean_intent_card(req.card.model_dump())
    if intent_card_empty(card):
        raise HTTPException(
            status_code=400, detail="理解卡还是空的:先让 AI 回读一次,或手填世界坐标与主角"
        )
    mode = "short" if req.mode == "short" else ("drama" if project.mode == "drama" else "serial")
    plans = project.book_plans or []
    plan: dict | None = None
    if req.index is not None:
        if req.index >= len(plans):
            raise HTTPException(status_code=400, detail="方向卡序号越界,先出一批方向")
        plan = plans[req.index]

    # 题材以卡里的显式值为准:不再让推断静默覆盖作者的世界坐标(问题 #1 的正面修复)
    if card.get("genre"):
        project.genre = str(card["genre"])[:100]
    project.intent_card = card
    project.mode = mode
    if plan is not None:
        project.brief = _render_order(plan, "short" if mode == "short" else "serial")
        # 书名跟着方向卡走,但**只在书名还是「自动落的那一版」时**才覆盖:
        # 空 / 未命名新书 / 等于任何一张方向卡的书名(说明它上一次是自动落的)。
        # 为什么不能只判「空/未命名」:作者换一套方向卡重确认时,书名已不是哨兵值,
        # 旧写法会让书名停在上一套卡上——「卡上是《假账》,书名却是《账房》」,
        # 正是旧链「方案卡的书名到了书名页又对不上」那类毛病的翻版(用户 2026-10-17 反馈)。
        # 作者自己写过的书名不在这三种情形里,因此不会被覆盖。
        plan_titles = {str(p.get("title") or "").strip() for p in plans}
        plan_titles.discard("")
        auto_titled = project.title in ("", _TITLE_SENTINEL) or project.title in plan_titles
        if auto_titled and str(plan.get("title") or "").strip():
            project.title = str(plan["title"]).strip()[:100]
    if not (project.brief or "").strip():
        raise HTTPException(
            status_code=400, detail="还没有开书订单:先挑一套方向卡(或让 AI 出三套)"
        )
    project.brief_confirmed = True

    # 篇幅:手选档 > 方向卡推荐档(与 plan-confirm 同一张映射表,口径不许分叉)
    if req.scale_override and req.scale_override.get("chapters") and req.scale_override.get("words"):
        project.target_chapters = int(req.scale_override["chapters"])
        project.target_words_per_chapter = int(req.scale_override["words"])
    elif plan is not None:
        scale = str(plan.get("scale") or "")
        if mode == "short":
            words = _SHORT_SCALE_MAP.get(scale)
            if words:
                project.target_chapters, project.target_words_per_chapter = 1, words
        else:
            pair = _SERIAL_SCALE_MAP.get(scale)
            if pair:
                project.target_chapters, project.target_words_per_chapter = pair

    # 起步流进度:停在样稿屏(列表页「继续创建」据此回到正确的一屏)
    project.setup_state = "sample"
    record_gen_inputs(
        kind="intent-confirm", project_id=project_id,
        payload={"card": card, "index": req.index, "mode": mode,
                 "scale_override": req.scale_override},
        db=db,
    )
    db.commit()
    db.refresh(project)

    job_id = spawn_sample_chain(project_id)
    return IntentConfirmResponse(
        project=ProjectOut.model_validate(project, from_attributes=True),
        sample_job_id=job_id,
    )
