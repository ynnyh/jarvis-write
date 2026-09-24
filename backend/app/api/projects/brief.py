# app/api/projects/brief.py
# -*- coding: utf-8 -*-
"""开书对话式确认流(确认链 L0,2026-09-24 重构开书入口交互)。

旧交互的病根:想法屏选流派后拼一句「按「XX」的套路来」直接跳概念屏自动抽
8 张引擎卡——AI 在近乎零信号下盲猜一批卡让作者挑,挑完才轮到人说话,
「出的东西不符合心意」是结构必然。本次把动画短剧工坊验证过的「对话式确认流」
移植到开书主线:

  作者的点子(哪怕一句)/🎲 AI 出点子兜底
    → 策划接住并补全成一版完整简介(每轮新草稿自动重新上锁)
    → 作者改一句、AI 重出一版,多轮聊
    → 作者拍板「✓ 简介就按这个来」(brief_confirmed=True)
    → 才解锁概念深化(concept-from-brief),架构/蓝图都排在这之后

门禁在后端:简介未拍板调 concept-from-brief 直接 409,前端锁不锁只是展示层。

端点:
  POST /api/projects/{pid}/brief-chat              一轮对话:回 reply + 新简介草稿
  POST /api/projects/{pid}/concept-from-brief-async 已拍板简介 → 六字段概念(job)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.inspire import DevelopRequest, _develop_impl
from app.auth import current_user_id, get_current_user
from app.db.models import Project
from app.db.session import get_db
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import dna_block_of, render_style_block
from app.jobs import spawn_job
from app.llm.router import Task, get_adapter_for
from app.prompts.inspire import BOOK_BRIEF_CHAT_PROMPT, _GENRE_BOUNDARY
from app.schemas.project import ProjectOut

from ._common import _get_project_or_404

logger = logging.getLogger("jarvis-write.brief")

router = APIRouter()

_CHAT_USER_MAX = 500   # 单条作者消息上限(与动画短剧简介聊天同口径)
_CHAT_KEEP = 40        # 线程最多保留条数(超出丢最旧的)


class BriefChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_CHAT_USER_MAX,
                         description="作者这一轮说的话(选点子时由前端拼成一条发言)")


class BriefChatResponse(BaseModel):
    reply: str
    brief: str
    project: ProjectOut


def _chat_block(chat: list) -> str:
    if not chat:
        return "(空,这是第一轮)"
    return "\n".join(
        f"{'作者' if m.get('role') == 'user' else '策划'}:{str(m.get('content') or '')[:800]}"
        for m in chat[-_CHAT_KEEP:]
        if isinstance(m, dict)
    )


def _require_confirmed_brief(project: Project) -> str:
    """concept-from-brief 的硬门:简介没拍板,后面的生成一概不放行(409)。"""
    brief = (project.brief or "").strip()
    if not brief:
        raise HTTPException(
            status_code=409,
            detail="还没有简介:先在简介屏和策划聊出一版(或让 AI 出点子挑一个)",
        )
    if not project.brief_confirmed:
        raise HTTPException(
            status_code=409,
            detail="简介还没拍板:过目、改到满意后点「✓ 简介就按这个来」再深化",
        )
    return brief


@router.post("/{project_id}/brief-chat", response_model=BriefChatResponse)
async def brief_chat(
    project_id: int, req: BriefChatRequest, db: Session = Depends(get_db)
) -> BriefChatResponse:
    """一轮简介对话:回一句引导(reply)+ 当前完整版简介草稿(brief)。

    草稿落库即重新上锁(brief_confirmed=False)——聊得再好,作者没点头,
    概念深化按钮就不亮(后端 409 同口径把关)。
    """
    project = _get_project_or_404(db, project_id)
    clean = req.message.strip()[:_CHAT_USER_MAX]
    history = [m for m in (project.chat_log or []) if isinstance(m, dict)]
    thread = history + [{"role": "user", "content": clean}]

    style_block = render_style_block(assemble_tendency("outline", project.global_tendency or {}))
    style_block += dna_block_of(project.dna)
    prompt = BOOK_BRIEF_CHAT_PROMPT.format(
        topic=(project.topic or "").strip() or "(空白,按已选方向自由引导)",
        brief=(project.brief or "").strip() or "(还没有,这轮先出第一版)",
        style_directives=style_block,
        chat_block=_chat_block(thread),
        genre_boundary=_GENRE_BOUNDARY,
    )
    from app.engines.consistency.extractor import parse_llm_json

    adapter = get_adapter_for(Task.ARCHITECTURE)
    try:
        data = parse_llm_json(await adapter.ask(prompt))
        reply = str(data.get("reply") or "").strip()[:1000]
        brief = str(data.get("brief") or "").strip()[:2000]
        if not reply or not brief:
            raise ValueError("模型回了空 reply/brief(空壳)")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"这轮没接住:{exc}") from exc

    # 拷贝-改-赋回:JSON 列原地改 SQLAlchemy 不认,commit 会空转(踩过的坑)
    project.chat_log = (thread + [{"role": "assistant", "content": reply}])[-_CHAT_KEEP:]
    project.brief = brief
    project.brief_confirmed = False  # 每出新草稿自动重新上锁
    db.commit()
    db.refresh(project)
    return BriefChatResponse(
        reply=reply, brief=brief, project=ProjectOut.model_validate(project, from_attributes=True)
    )


@router.post("/{project_id}/concept-from-brief-async")
async def concept_from_brief_async(
    project_id: int, db: Session = Depends(get_db)
) -> dict:
    """已拍板简介 → 深化成六字段概念(强模型 job)。简介未拍板 409。"""
    project = _get_project_or_404(db, project_id)
    brief = _require_confirmed_brief(project)  # 硬门:拍板才算「照单生成」的订单
    uid = current_user_id.get()

    async def work(progress):
        progress("AI 正在把拍板的简介深化成完整概念")
        result = await _develop_impl(DevelopRequest(
            brief=brief,
            spark=(project.topic or "").strip(),
            tendency=project.global_tendency or {},
            dna=project.dna,
        ))
        return result.model_dump()

    return {"job_id": spawn_job(f"inspire-develop-u{uid}", work)}
