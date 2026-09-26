# app/api/projects/plans.py
# -*- coding: utf-8 -*-
"""开书方案流(确认链 L0 新形态,docs/22 P0):三问定纲 → 整书方案×3 → 拍板。

替代简介逐格访谈(BOOK_BRIEF_CHAT_PROMPT 退役为深聊入口,端点保留):
沟通量不降——细节确认密度反而更高(三套方案每套 9 个格子全填满)——但用户的
显式动作收敛为「点候选 → 选方案 → 拍板」三次。prompt 草案经真实模型去险实验
验证(backend/scripts/exp_book_plans.py,2026-09-26):六轮 JSON 全一次解析、
三套两两相似度 0.055-0.10、定向修订服从度好(未要求字段不动)。

拍板语义迁移:选中方案渲染成开书订单写入 brief、brief_confirmed=True——
下游概念深化(/concept-from-brief)的 409 硬门原样复用,架构/蓝图链路零改动。

端点:
  POST /api/projects/{pid}/three-questions  三问定纲:每问 3-4 候选 + ★首推(FAST)
  POST /api/projects/{pid}/book-plans       整书方案×3(ARCHITECTURE;再来三套带 avoid)
  POST /api/projects/{pid}/revise-plan      定向修订:一句话只改第 index 套(FAST)
  POST /api/projects/{pid}/plan-confirm     拍板:方案 → 开书订单,唯一前置硬门
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Project
from app.db.session import get_db
from app.engines.consistency.extractor import parse_llm_json
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import dna_block_of, render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts.inspire import (
    _GENRE_BOUNDARY,
    BOOK_PLANS_PROMPT,
    BOOK_PLANS_SHORT_PROMPT,
    REVISE_PLAN_PROMPT,
    THREE_QUESTIONS_PROMPT,
)
from app.schemas.project import ProjectOut

from ._common import _get_project_or_404

logger = logging.getLogger("jarvis-write.plans")

router = APIRouter()

_ANSWER_MAX = 200      # 三问每答上限(点选候选文本或自写)
_DIRECTIVE_MAX = 300   # 定向修订一句话上限
_TITLE_SENTINEL = "未命名新书"

# 档位 → (章数, 每章字数);与前端 SCALE_PRESETS 同源,后端为拍板落库的权威映射
_SERIAL_SCALE_MAP = {
    "短篇": (20, 3000),
    "中篇": (60, 3000),
    "长篇": (150, 3000),
    "连载": (500, 3000),
}
# 短故事篇幅 → (章数=1, 每章字数)
_SHORT_SCALE_MAP = {
    "3千字": 3000,
    "8千字": 8000,
    "1万5": 15000,
    "2万": 20000,
}

_PLAN_FIELDS_SERIAL = ["title", "kernel", "protagonist", "world", "arc", "engine"]
_PLAN_FIELDS_SHORT = ["title", "kernel", "protagonist", "world", "arc", "ending"]

_Q3_TITLE = {
    "serial": "最大的坎是什么(核心困境的形态+破局点的反差)",
    "short": "结尾想落在什么感觉上(结局情绪定调)",
}


def _style_block_of(project: Project) -> str:
    block = render_style_block(assemble_tendency("outline", project.global_tendency or {}))
    block += dna_block_of(project.dna)
    return block


def _q3_title(mode: str) -> str:
    return _Q3_TITLE.get(mode, _Q3_TITLE["serial"])


class ThreeQuestionsRequest(BaseModel):
    mode: str = Field(default="serial", max_length=10)
    topic: str = Field(default="", max_length=500)
    genre: str = Field(default="", max_length=100)
    # 防趋同(docs/22「🎲换一批」):上一批已展示的候选文本,注入 prompt 要求换角度
    avoid: list[str] = Field(default_factory=list)


class QuestionCandidate(BaseModel):
    text: str
    recommended: bool = False
    reason: str = ""


class Question(BaseModel):
    key: str
    title: str
    candidates: list[QuestionCandidate]


class ThreeQuestionsResponse(BaseModel):
    questions: list[Question]


def _sanitize_questions(data: dict, mode: str) -> list[Question]:
    raw = data.get("questions") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions 为空")
    out: list[Question] = []
    for q in raw:
        cands = [
            QuestionCandidate(
                text=str(c.get("text") or "").strip()[:120],
                recommended=bool(c.get("recommended")),
                reason=str(c.get("reason") or "").strip()[:150],
            )
            for c in (q.get("candidates") or [])
            if str(c.get("text") or "").strip()
        ]
        if not cands:
            continue
        # 首推唯一化:模型标多个时只认第一个;一个都没标就认第一个候选
        seen_rec = False
        for c in cands:
            if c.recommended and not seen_rec:
                seen_rec = True
            else:
                c.recommended = False
        if not seen_rec and cands:
            cands[0].recommended = True
        out.append(
            Question(
                key=str(q.get("key") or f"q{len(out) + 1}"),
                title=str(q.get("title") or "").strip()[:60],
                candidates=cands[:4],
            )
        )
    if not out:
        raise ValueError("没有可用的问题")
    return out


@router.post("/{project_id}/three-questions", response_model=ThreeQuestionsResponse)
async def three_questions(
    project_id: int, req: ThreeQuestionsRequest, db: Session = Depends(get_db)
) -> ThreeQuestionsResponse:
    """三问定纲(发散档,高温度):每问 3-4 候选 + ★首推带理由,第 3 问随模式分叉。

    温度显式提到 0.9:三问是纯发散创意任务,SUMMARY 档默认 0.3(忠实压缩)
    会让「🎲换一批」每次出同一批(2026-09-26 作者实测反馈);avoid 注入上一批
    候选防趋同——与提案通道(avoid_block)同一套机制。
    """
    project = _get_project_or_404(db, project_id)
    mode = "short" if req.mode == "short" else "serial"
    context = req.topic.strip() or "(空白——按你的判断给方向)"
    if req.genre.strip():
        context += f"\n[已选题材: {req.genre.strip()}]"
    avoid_block = ""
    if req.avoid:
        avoid_block = (
            "【避开清单(作者已看过的上一批候选,本次严禁再出同义或换皮版本)】\n- "
            + "\n- ".join(a.strip()[:60] for a in req.avoid[:12] if a.strip())
        )
    prompt = THREE_QUESTIONS_PROMPT.format(
        context=context,
        style_directives=_style_block_of(project),
        avoid_block=avoid_block,
        q3_title=_q3_title(mode),
        genre_boundary=_GENRE_BOUNDARY if req.genre.strip() else (
            "题材未定,方向可自由发挥;但同样不吃老套路(觉醒/系统/重生/穿越,除非作者明确要求)。"
        ),
    )
    # 发散任务借道 SUMMARY 档(FAST)但强制高温度——0.3 的摘要档「换一批」必出同批
    adapter = get_adapter_for(Task.SUMMARY, temperature=0.9)
    try:
        questions = _sanitize_questions(parse_llm_json(await adapter.ask(prompt)), mode)
    except Exception as exc:  # noqa: BLE001
        logger.warning("three-questions 失败 pid=%s: %s", project_id, exc)
        raise HTTPException(status_code=502, detail=f"三问没接住:{exc}") from exc
    return ThreeQuestionsResponse(questions=questions)


class BookPlansRequest(BaseModel):
    mode: str = Field(default="serial", max_length=10)
    topic: str = Field(default="", max_length=500)
    genre: str = Field(default="", max_length=100)
    answers: dict[str, str] = Field(default_factory=dict)
    # 再来三套时的反馈(可空);avoid 为上一批的 label/title/kernel 摘要(防趋同,
    # 2026-09-26 作者实测「换汤不换药」:标签级避开会被换皮绕过,须给到内核结构)
    feedback: str = Field(default="", max_length=_DIRECTIVE_MAX)
    avoid: list[str] = Field(default_factory=list)


class PlanCard(BaseModel):
    title: str
    kernel: str
    protagonist: str
    world: str
    arc: str
    engine: str = ""
    ending: str = ""
    flavor: list[str] = []
    scale: str = ""
    scale_reason: str = ""
    label: str = ""


class BookPlansResponse(BaseModel):
    plans: list[PlanCard]
    project: ProjectOut


def _sanitize_plans(data: dict, mode: str) -> list[PlanCard]:
    raw = data.get("plans") or []
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("plans 少于 2 套")
    fields = _PLAN_FIELDS_SHORT if mode == "short" else _PLAN_FIELDS_SERIAL
    out: list[PlanCard] = []
    for p in raw:
        if not all(str(p.get(f) or "").strip() for f in fields):
            continue
        flavor = p.get("flavor") or []
        if isinstance(flavor, str):
            flavor = [flavor]
        out.append(
            PlanCard(
                **{f: str(p.get(f) or "").strip()[:600] for f in fields},
                flavor=[str(x).strip()[:20] for x in flavor if str(x).strip()][:4],
                scale=str(p.get("scale") or "").strip()[:10],
                scale_reason=str(p.get("scale_reason") or "").strip()[:150],
                label=str(p.get("label") or "").strip()[:60],
            )
        )
    if len(out) < 2:
        raise ValueError("字段齐全的方案少于 2 套")
    return out


@router.post("/{project_id}/book-plans", response_model=BookPlansResponse)
async def book_plans(
    project_id: int, req: BookPlansRequest, db: Session = Depends(get_db)
) -> BookPlansResponse:
    """整书方案×3(ARCHITECTURE 档):填满细节的三套完整方案,存为当前工作集。"""
    project = _get_project_or_404(db, project_id)
    mode = "short" if req.mode == "short" else "serial"
    topic = req.topic.strip() or "(空白——按你的判断给方向)"
    if req.genre.strip():
        topic += f"\n[已选题材: {req.genre.strip()}]"
    if req.feedback.strip():
        topic += f"\n[作者对上一批的反馈: {req.feedback.strip()}]"
    if req.avoid:
        topic += (
            "\n【避开清单——作者已看过上一批,严禁换皮重出】\n- "
            + "\n- ".join(a.strip()[:80] for a in req.avoid[:6] if a.strip())
            + "\n新一批的每一套,其主角身份/行业/地域、冲突来源、内核结构、破局反差"
            "都必须与避开清单里的每一套明显不同;严禁只换人名与措辞的「换皮」;"
            "上一批出现过的味道组合不得原样复用。"
        )
    answers = "\n".join(
        f"{k}: {str(v).strip()[:_ANSWER_MAX]}"
        for k, v in sorted(req.answers.items())
        if str(v).strip()
    ) or "(三问全跳过——按你的判断定)"
    prompt_tpl = BOOK_PLANS_SHORT_PROMPT if mode == "short" else BOOK_PLANS_PROMPT
    prompt = prompt_tpl.format(
        topic=topic,
        answers=answers,
        style_directives=_style_block_of(project),
        genre_boundary=_GENRE_BOUNDARY if req.genre.strip() else (
            "题材未定,方向可自由发挥;但同样不吃老套路(觉醒/系统/重生/穿越,除非作者明确要求)。"
        ),
    )
    adapter = get_adapter_for(Task.ARCHITECTURE)
    try:
        plans = _sanitize_plans(parse_llm_json(await adapter.ask(prompt)), mode)
    except Exception as exc:  # noqa: BLE001
        logger.warning("book-plans 失败 pid=%s: %s", project_id, exc)
        raise HTTPException(status_code=502, detail=f"方案没出好:{exc}") from exc
    # 拷贝-改-赋回:JSON 列原地改 SQLAlchemy 不认
    project.book_plans = [p.model_dump() for p in plans]  # type: ignore[assignment]
    db.commit()
    db.refresh(project)
    return BookPlansResponse(plans=plans, project=ProjectOut.model_validate(project, from_attributes=True))


class RevisePlanRequest(BaseModel):
    index: int = Field(ge=0, le=9)
    directive: str = Field(min_length=1, max_length=_DIRECTIVE_MAX)


class RevisePlanResponse(BaseModel):
    plans: list[PlanCard]
    project: ProjectOut


@router.post("/{project_id}/revise-plan", response_model=RevisePlanResponse)
async def revise_plan(
    project_id: int, req: RevisePlanRequest, db: Session = Depends(get_db)
) -> RevisePlanResponse:
    """定向修订(FAST 档):一句话只改第 index 套,其余套与未要求字段不动。"""
    project = _get_project_or_404(db, project_id)
    plans_raw = project.book_plans or []
    if req.index >= len(plans_raw):
        raise HTTPException(status_code=400, detail="方案序号越界,先出一批方案")
    mode = "short" if project.mode == "short" else "serial"
    target = plans_raw[req.index]
    shape_fields = _PLAN_FIELDS_SHORT if mode == "short" else _PLAN_FIELDS_SERIAL + ["engine"]
    json_shape = "{" + ", ".join(f'"{f}": ""' for f in shape_fields) + \
        ', "flavor": [""], "scale": "", "scale_reason": "", "label": ""}'
    prompt = REVISE_PLAN_PROMPT.format(
        plan_kind="短故事方案" if mode == "short" else "整书方案",
        plan_json=target,
        directive=req.directive.strip(),
        json_shape=json_shape,
    )
    adapter = get_adapter_for(Task.POLISH, max_tokens=4096)
    try:
        revised = parse_llm_json(await adapter.ask(prompt))
    except Exception as exc:  # noqa: BLE001
        logger.warning("revise-plan 失败 pid=%s: %s", project_id, exc)
        raise HTTPException(status_code=502, detail=f"这轮没改好:{exc}") from exc
    # 修订合并:模型返回的非空字段采纳,空/缺字段回填原值(不让模型丢格子);
    # 「未要求字段逐字保留」由 prompt 约束,实验验证服从度良好
    merged = dict(target)
    for k, v in revised.items():
        if k in target and isinstance(target[k], str) and isinstance(v, str):
            if v.strip():
                merged[k] = v.strip()
        elif k == "flavor" and isinstance(v, list):
            merged[k] = [str(x).strip()[:20] for x in v if str(x).strip()][:4] or target.get("flavor")
        elif k in ("scale", "scale_reason", "label") and isinstance(v, str) and v.strip():
            merged[k] = v.strip()
    plans = plans_raw.copy()
    plans[req.index] = merged
    project.book_plans = plans  # type: ignore[assignment]
    db.commit()
    db.refresh(project)
    return RevisePlanResponse(
        plans=[PlanCard(**p) for p in plans],
        project=ProjectOut.model_validate(project, from_attributes=True),
    )


class PlanConfirmRequest(BaseModel):
    index: int = Field(ge=0, le=9)
    mode: str = Field(default="serial", max_length=10)
    # 用户在屏 0 手选过档位时传入,优先于方案自带推荐档
    scale_override: dict[str, int] | None = None


def _render_order(plan: dict, mode: str) -> str:
    """方案 → 开书订单文本。下游概念深化把它当「作者已拍板的最高约束」注入。"""
    flavor = " · ".join(plan.get("flavor") or [])
    if mode == "short":
        return (
            f"书名(暂定):《{plan.get('title', '')}》(短故事,一次讲完)\n"
            f"【故事内核】{plan.get('kernel', '')}\n"
            f"【主角】{plan.get('protagonist', '')}\n"
            f"【故事弧】{plan.get('arc', '')}\n"
            f"【舞台】{plan.get('world', '')}\n"
            f"【味道与结尾】{flavor} · 结尾落在:{plan.get('ending', '')}"
        )
    return (
        f"书名(暂定):《{plan.get('title', '')}》\n"
        f"【故事内核】{plan.get('kernel', '')}\n"
        f"【主角】{plan.get('protagonist', '')}\n"
        f"【首卷走向】{plan.get('arc', '')}\n"
        f"【世界观底盘】{plan.get('world', '')}\n"
        f"【味道与连载引擎】{flavor} · {plan.get('engine', '')}"
    )


@router.post("/{project_id}/plan-confirm", response_model=ProjectOut)
async def plan_confirm(
    project_id: int, req: PlanConfirmRequest, db: Session = Depends(get_db)
) -> Project:
    """拍板:选中方案渲染成开书订单 → brief_confirmed=True(全链唯一前置硬门)。"""
    project = _get_project_or_404(db, project_id)
    plans = project.book_plans or []
    if req.index >= len(plans):
        raise HTTPException(status_code=400, detail="方案序号越界,先出一批方案")
    mode = "short" if req.mode == "short" else "serial"
    plan = plans[req.index]

    project.mode = mode
    project.brief = _render_order(plan, mode)
    project.brief_confirmed = True
    if project.title in ("", _TITLE_SENTINEL) and str(plan.get("title") or "").strip():
        project.title = str(plan["title"]).strip()[:100]
    # 篇幅:屏 0 手选 > 方案推荐档;连载/短故事的档位映射见 _SERIAL/_SHORT_SCALE_MAP
    if req.scale_override and req.scale_override.get("chapters") and req.scale_override.get("words"):
        project.target_chapters = int(req.scale_override["chapters"])
        project.target_words_per_chapter = int(req.scale_override["words"])
    else:
        scale = str(plan.get("scale") or "")
        if mode == "short":
            words = _SHORT_SCALE_MAP.get(scale)
            if words:
                project.target_chapters, project.target_words_per_chapter = 1, words
        else:
            pair = _SERIAL_SCALE_MAP.get(scale)
            if pair:
                project.target_chapters, project.target_words_per_chapter = pair
    db.commit()
    db.refresh(project)
    return project
