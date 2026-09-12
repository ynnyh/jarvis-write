# app/api/projects/premise.py
# -*- coding: utf-8 -*-
"""核心梗卡:一本书的「纲」——高概念 / 兑现机制 / 边界禁忌 / 钩子计划。

- GET    /premise          读主梗(没有则 null,前端据此出「AI 补建」入口);
- PUT    /premise          保存(作者改过即 source=human,AI 建议不再覆盖);
- POST   /suggest-premise  AI 从概念+题材提炼三件套草稿(轻量调用,**不落库**,
                           前端确认后走 PUT 保存——与 suggest-shape 同款"建议/确认分离")。

梗是纲:蓝图逐章标「梗兑现」拍、正文注入本章节拍+边界、交稿对账、体检梗健康度,
全部以这里的记录为唯一事实源。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import Outline, User
from app.db.models.premise import Premise
from app.db.session import SessionLocal, get_db
from app.engines.consistency.extractor import parse_llm_json
from app.jobs import list_running, spawn_job
from app.llm.router import Task, get_adapter_for

from ._common import _get_project_or_404

router = APIRouter()

_VALID_BEATS = 6
_SUGGEST_PROMPT = """\
你是资深网文责编。根据下面这本书的概念与题材,提炼「核心梗卡」三件套。

【一句话主线】{logline}
【设定】{setting}
【题材】{genre}

什么是核心梗:高概念是把读者点进来的钩子;兑现机制是这个梗为什么能反复产生
冲突与满足(能力边界、代价累积、对手结构);边界禁忌是写什么会把梗写崩。
拍 = 梗的兑现节拍,全书反复循环、逐级抬升,3-6 个,名字要短(2-8 字)。

严格输出 JSON(不要 markdown 围栏,不要解释):
{{"high_concept": "高概念一句话,50字内",
  "payoff": "兑现机制说明,80字内:这个梗为什么能反复产生冲突与满足",
  "beats": ["节拍名", "节拍名", "节拍名"],
  "boundaries": ["边界禁忌", "边界禁忌"],
  "hook_plan": {{"opening": "开局钩一句话", "mid": "中期反转一句话", "climax": "大高潮一句话"}}}}
"""


class PremiseOut(BaseModel):
    high_concept: str = ""
    payoff: str = ""
    beats: list[str] = []
    boundaries: list[str] = []
    hook_plan: dict = {}
    source: str = "ai"


class PremiseIn(BaseModel):
    high_concept: str = Field(default="", max_length=200)
    payoff: str = Field(default="", max_length=500)
    beats: list[str] = Field(default_factory=list, max_length=8)
    boundaries: list[str] = Field(default_factory=list, max_length=8)
    hook_plan: dict = Field(default_factory=dict)


def _get_main_premise(db: Session, project_id: int) -> Premise | None:
    return (
        db.query(Premise)
        .filter(Premise.project_id == project_id, Premise.kind == "main")
        .first()
    )


def _clean_str_list(items: list, cap: int = 8, item_cap: int = 60) -> list[str]:
    out: list[str] = []
    for it in items or []:
        s = " ".join(str(it or "").split())
        if s:
            out.append(s[:item_cap])
    return out[:cap]


@router.get("/{project_id}/premise", response_model=PremiseOut | None)
def get_premise(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读主梗卡;未建返回 null(前端据此出「AI 补建」入口)。"""
    project = _get_project_or_404(db, project_id)
    row = _get_main_premise(db, project.id)
    if row is None:
        return None
    return PremiseOut(
        high_concept=row.high_concept,
        payoff=row.payoff,
        beats=row.beats or [],
        boundaries=row.boundaries or [],
        hook_plan=row.hook_plan or {},
        source=row.source,
    )


@router.put("/{project_id}/premise", response_model=PremiseOut)
def save_premise(
    project_id: int,
    req: PremiseIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存主梗卡(全量覆盖)。作者保存即 source=human——AI 建议此后不再覆盖。"""
    project = _get_project_or_404(db, project_id)
    row = _get_main_premise(db, project.id)
    if row is None:
        row = Premise(project_id=project.id, kind="main")
        db.add(row)
    row.high_concept = req.high_concept.strip()
    row.payoff = req.payoff.strip()
    row.beats = _clean_str_list(req.beats, cap=_VALID_BEATS)
    row.boundaries = _clean_str_list(req.boundaries)
    hook_plan = req.hook_plan if isinstance(req.hook_plan, dict) else {}
    row.hook_plan = {
        k: " ".join(str(hook_plan.get(k) or "").split())[:120]
        for k in ("opening", "mid", "climax")
        if str(hook_plan.get(k) or "").strip()
    }
    row.source = "human"
    db.commit()
    return PremiseOut(
        high_concept=row.high_concept,
        payoff=row.payoff,
        beats=row.beats or [],
        boundaries=row.boundaries or [],
        hook_plan=row.hook_plan or {},
        source=row.source,
    )


@router.post("/{project_id}/suggest-premise", response_model=PremiseOut)
async def suggest_premise(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 从概念+题材提炼梗卡草稿。**不落库**——前端确认后走 PUT 保存。"""
    project = _get_project_or_404(db, project_id)
    concept = project.concept or {}
    logline = str(concept.get("logline") or project.topic or "")[:300]
    setting = str(concept.get("setting") or "")[:200]
    if not (logline or setting):
        raise HTTPException(status_code=400, detail="先确认故事概念,再提炼核心梗")

    prompt = _SUGGEST_PROMPT.format(
        logline=logline or "(未填写)",
        setting=setting or "(未填写)",
        genre=project.genre or "不限",
    )
    adapter = get_adapter_for(Task.SUMMARY, max_tokens=500, timeout=60)
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 失败原因直接反馈给前端
        raise HTTPException(status_code=502, detail=f"梗卡提炼失败: {exc}") from exc

    data = parse_llm_json(raw) or {}
    hook_plan = data.get("hook_plan") if isinstance(data.get("hook_plan"), dict) else {}
    return PremiseOut(
        high_concept=str(data.get("high_concept") or "").strip()[:200],
        payoff=str(data.get("payoff") or "").strip()[:500],
        beats=_clean_str_list(data.get("beats") or [], cap=_VALID_BEATS),
        boundaries=_clean_str_list(data.get("boundaries") or []),
        hook_plan={
            k: " ".join(str(hook_plan.get(k) or "").split())[:120]
            for k in ("opening", "mid", "climax")
            if str(hook_plan.get(k) or "").strip()
        },
        source="ai",
    )


# ---------- 全书补标节拍(存量书兜底):后台 job,逐批给未标章打「梗兑现」 ----------

_BACKFILL_KIND = "premise-backfill-{pid}"
_BACKFILL_BATCH = 20

_BACKFILL_PROMPT = """\
你在给一部小说的存量章节蓝图补标「梗兑现」。

【全书核心梗】
高概念:{high_concept}
兑现机制:{payoff}
兑现节拍表:{beats}

【待标章节】(每行:章号|简述|戏核)
{chapters}

要求:对照节拍表,判断每章推进了核心梗的哪一拍;纯过渡章标"无"。
严格输出 JSON(不要围栏不要解释),键为章号(字符串),值为「第N拍·拍名——一句话依据」:
{{"3": "第1拍·代价显形——林夏首次看到自己腕上的倒计时延长", "5": "无"}}
"""


@router.post("/{project_id}/premise/backfill-beats")
async def backfill_premise_beats(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """给未标「梗兑现」的存量章节后台补标(轻量模型,逐批 20 章)。"""
    project = _get_project_or_404(db, project_id)
    premise = _get_main_premise(db, project.id)
    if premise is None or not (premise.high_concept or "").strip():
        raise HTTPException(status_code=400, detail="先建立核心梗卡,再补标节拍")

    kind = _BACKFILL_KIND.format(pid=project.id)
    if list_running(kind):
        raise HTTPException(status_code=409, detail="补标任务正在进行中,请稍候")

    user_id = user.id

    async def work(progress) -> dict:
        # worker 自己开 Session(跨 LLM 调用持有请求级 session 是 database is locked 老根因)
        with SessionLocal() as session:
            outlines = (
                session.query(Outline)
                .filter(
                    Outline.project_id == project.id,
                    Outline.premise_beat.is_(""),
                )
                .order_by(Outline.chapter_number)
                .all()
            )
            if not outlines:
                return {"marked": 0, "total": 0}

            high = premise.high_concept.strip()
            payoff = (premise.payoff or "").strip()
            beats = "、".join(
                f"第{i + 1}拍·{str(b).strip()}" for i, b in enumerate(premise.beats or []) if str(b).strip()
            )
            adapter = get_adapter_for(Task.SUMMARY, max_tokens=800, timeout=90)

            marked = 0
            for i in range(0, len(outlines), _BACKFILL_BATCH):
                batch = outlines[i: i + _BACKFILL_BATCH]
                rows = "\n".join(
                    f"{o.chapter_number}|{(o.summary or '')[:60]}|{(o.scene_anchor or '')[:40]}"
                    for o in batch
                )
                prompt = _BACKFILL_PROMPT.format(
                    high_concept=high, payoff=payoff or "(未填)", beats=beats or "(未填)", chapters=rows,
                )
                progress(f"补标第 {batch[0].chapter_number}-{batch[-1].chapter_number} 章")
                raw = await adapter.ask(prompt)
                data = parse_llm_json(raw) or {}
                for o in batch:
                    beat = str(data.get(str(o.chapter_number)) or "").strip()
                    if beat and beat not in ("无",):
                        o.premise_beat = beat[:200]
                        marked += 1
                session.commit()
            return {"marked": marked, "total": len(outlines)}

    job_id = spawn_job(kind, work)
    return {"job_id": job_id}
