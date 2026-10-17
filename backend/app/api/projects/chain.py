# app/api/projects/chain.py
# -*- coding: utf-8 -*-
"""开书生成链:先小后大(docs/30 / docs/32 §2.2)。

样稿链(sample-chain):概念 → 梗卡 → 核心种子 → 前三章蓝图 → 前三章正文。
分钟级先出「能读的东西」——传国玉玺案例里最刺眼的体验问题是铺 150 章蓝图的
7.4 分钟干等发生在**读到正文之前**,作者根本还没确认这本书对不对就被罚站。

全链(full-chain):其余三层架构 → 骨架 → 全书蓝图,在试读确认②之后后台跑。

两条链都幂等:已有产物且本次不带 directive 就跳过;防重入走 list_running。
每一步自开短事务(session 开合随步骤),**绝不跨 LLM 调用持有 session**
(CLAUDE.md「database is locked」的老根因)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.inspire import DevelopRequest, _develop_impl
from app.audit import record_gen_inputs
from app.db.models import Chapter, Outline, Premise, Project
from app.db.session import SessionLocal, get_db
from app.engines.consistency.extractor import parse_llm_json
from app.engines.intent import render_intent_block
from app.engines.pipeline.architecture import (
    LAYER_LABEL,
    confirm_architecture_layer,
    generate_architecture_layer,
    layer_state,
    save_architecture_layer,
)
from app.engines.pipeline.chapter import generate_chapter
from app.engines.skills.packs import render_project_skill_block
from app.jobs import (
    create_job,
    fail_job,
    finish_job,
    fire_and_track,
    list_running,
    normalize_job_error,
    update_stage,
)
from app.llm.router import Task, get_adapter_for
from app.schemas.concept import coerce_concept

from ._common import _get_project_or_404
from .architecture import _extract_personas_safe
from .blueprint import run_blueprint_pipeline
from .premise import _SUGGEST_PROMPT, _VALID_BEATS, _clean_str_list, _get_main_premise
from .skeleton import build_skeleton_segments

logger = logging.getLogger("jarvis-write.chain")

router = APIRouter()

# 样稿先写几章:读到正文才算「能判断对不对」,3 章是「看得到开局+第一回报」的最小量
_SAMPLE_CHAPTERS = 3


def _sample_target(p: Project) -> int:
    return max(1, min(_SAMPLE_CHAPTERS, p.target_chapters or 1))


def _written_chapters(session: Session, project_id: int) -> list[int]:
    rows = (
        session.query(Chapter.chapter_number)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    return [int(r[0]) for r in rows]


def _has_outlines(session: Session, project_id: int) -> bool:
    return (
        session.query(Outline.id).filter(Outline.project_id == project_id).first()
        is not None
    )


# ---------- 样稿链各步(单步可测,各自开短事务由调用方 commit) ----------


async def _step_concept(session: Session, p: Project, report) -> None:
    """概念深化:拍板订单 → 六字段概念。理解卡拼在订单之后当硬约束。"""
    report("深化故事概念")
    result = await _develop_impl(DevelopRequest(
        brief=((p.brief or "").strip() + render_intent_block(p)),
        spark=(p.topic or "").strip(),
        tendency=p.global_tendency or {},
        dna=p.dna,
    ))
    p.concept = result.concept.model_dump()
    session.commit()


async def _step_premise(session: Session, p: Project, report) -> None:
    """梗卡:概念 → 高概念/兑现机制/边界禁忌。与 /suggest-premise 同一 prompt 口径。"""
    report("提炼核心梗卡")
    concept = p.concept or {}
    logline = str(concept.get("logline") or p.topic or "")[:300]
    setting = str(concept.get("setting") or "")[:200]
    prompt = _SUGGEST_PROMPT.format(
        logline=logline or "(未填写)",
        setting=setting or "(未填写)",
        genre=p.genre or "不限",
        skill_block=render_project_skill_block(session, p, "idea"),
    ) + render_intent_block(p)
    data = parse_llm_json(
        await get_adapter_for(Task.SUMMARY, max_tokens=500, timeout=60).ask(prompt)
    ) or {}
    hook_plan = data.get("hook_plan") if isinstance(data.get("hook_plan"), dict) else {}
    row = _get_main_premise(session, p.id)
    if row is None:
        row = Premise(project_id=p.id, kind="main")
        session.add(row)
    row.high_concept = str(data.get("high_concept") or "").strip()[:200]
    row.payoff = str(data.get("payoff") or "").strip()[:500]
    row.beats = _clean_str_list(data.get("beats") or [], cap=_VALID_BEATS)
    row.boundaries = _clean_str_list(data.get("boundaries") or [])
    row.hook_plan = {
        k: " ".join(str(hook_plan.get(k) or "").split())[:120]
        for k in ("opening", "mid", "climax")
        if str(hook_plan.get(k) or "").strip()
    }
    row.source = "ai"
    session.commit()


async def _step_core_seed(session: Session, p: Project, report) -> None:
    """核心种子(架构第一层):生成后自动认下——产物层不再是闸门。"""
    report("定核心种子")
    text = await generate_architecture_layer(
        layer="core_seed",
        topic=p.topic,
        genre=p.genre,
        number_of_chapters=p.target_chapters,
        word_number=p.target_words_per_chapter,
        concept=p.concept,
        global_tendency=p.global_tendency,
        dna=p.dna,
        open_ended=p.open_ended,
        intent_block=render_intent_block(p),
    )
    save_architecture_layer(session, p, "core_seed", text)
    confirm_architecture_layer(session, p, "core_seed", True)
    session.commit()


async def _step_sample_blueprint(
    session: Session, p: Project, report, *, directive: str = ""
) -> None:
    """前三章蓝图:只铺到样稿章数,不惊动卷纲(长书的卷纲留给全链后台)。"""
    report("铺前三章蓝图")
    await run_blueprint_pipeline(
        session, p,
        directive=directive,
        report=report,
        end_chapter_limit=_sample_target(p),
    )
    session.commit()


async def _step_sample_chapters(
    session: Session, p: Project, report, *, force: bool = False
) -> None:
    """前三章正文:逐章跑完整生成链(草稿→定稿→门禁→章后链路)。"""
    for n in range(1, _sample_target(p) + 1):
        row = (
            session.query(Chapter)
            .filter(Chapter.project_id == p.id, Chapter.chapter_number == n)
            .first()
        )
        if not force and row is not None and (row.final_content or "").strip():
            continue
        report(f"写第 {n} 章正文")
        await generate_chapter(
            session, p, n, None,
            progress=lambda s, _n=n: report(f"第 {_n} 章:{s}"),
        )
        session.commit()


# ---------- 样稿链任务 ----------


class SampleChainRequest(BaseModel):
    # 带话重出(「开场不对」时说一句):只重出蓝图与正文,已定的概念/种子不动
    directive: str = Field(default="", max_length=500)
    # "" = 幂等(已有就跳过);"blueprint" = 重出蓝图+正文;"all" = 全部重出
    restart: str = Field(default="", max_length=20)


def spawn_sample_chain(
    project_id: int, *, restart: str = "", directive: str = ""
) -> str:
    """启动样稿链(幂等去重)。返回 job_id。"""
    for jid, _job in list_running(f"sample-chain-{project_id}"):
        return jid
    job_id = create_job(f"sample-chain-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            record_gen_inputs(
                kind="sample-chain", project_id=project_id,
                payload={"restart": restart, "directive": directive},
                db=session,
            )
            session.commit()
            report = lambda s: update_stage(job_id, s)  # noqa: E731
            force_all = restart == "all"
            force_tail = restart in ("all", "blueprint")

            if force_all or coerce_concept(p.concept).is_empty():
                await _step_concept(session, p, report)
            if force_all or _get_main_premise(session, p.id) is None:
                await _step_premise(session, p, report)
            arch = p.architecture
            if force_all or arch is None or not (arch.core_seed or "").strip():
                await _step_core_seed(session, p, report)
            if force_tail or not _has_outlines(session, p.id):
                await _step_sample_blueprint(session, p, report, directive=directive)
            await _step_sample_chapters(session, p, report, force=force_tail)
            finish_job(job_id, {
                "chapters": _sample_target(p),
                "restart": restart,
                "written": _written_chapters(session, p.id),
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return job_id


@router.post("/{project_id}/sample-chain")
async def sample_chain(
    project_id: int, req: SampleChainRequest, db: Session = Depends(get_db)
) -> dict:
    """样稿链:概念 → 梗卡 → 核心种子 → 前三章蓝图 → 前三章正文。

    确认①之后由 intent-confirm 自动启动;作者在样稿屏也能手动重出
    (restart=blueprint 带一句 directive 只重出蓝图与正文)。
    """
    project = _get_project_or_404(db, project_id)
    if not project.brief_confirmed:
        raise HTTPException(status_code=409, detail="理解卡还没确认:先在理解屏点「✓ 就按这个来」")
    return {"job_id": spawn_sample_chain(
        project_id, restart=req.restart, directive=req.directive
    )}


# ---------- 全链任务 ----------


def spawn_full_chain(project_id: int) -> str:
    """启动全链(其余三层架构 → 骨架 → 全书蓝图),幂等去重。返回 job_id。"""
    for jid, _job in list_running(f"full-chain-{project_id}"):
        return jid
    job_id = create_job(f"full-chain-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            p = session.get(Project, project_id)
            record_gen_inputs(
                kind="full-chain", project_id=project_id, payload={}, db=session,
            )
            session.commit()
            report = lambda s: update_stage(job_id, s)  # noqa: E731
            intent_block = render_intent_block(p)
            sample_n = _sample_target(p)
            written = _written_chapters(session, p.id)
            # 已写正文是既成事实:后续层必须与之相容,不许推翻重来
            fact_block = ""
            if written:
                fact_block = (
                    f"\n注意:第 {'、'.join(str(n) for n in written)} 章正文已经写好并作为"
                    "本书开篇定稿,是既成事实——后续各层必须与这些已发生的人物、世界与开局"
                    "相容,不得推翻或改写。\n"
                )

            # 1) 其余三层架构:逐层生成 + 自动认下(产物层不是闸门)
            state = layer_state(p.architecture)
            for layer in ("character_dynamics", "world_building", "plot_architecture"):
                arch = p.architecture
                if state.get(layer) and (getattr(arch, layer) or "").strip():
                    continue
                report(f"架构:{LAYER_LABEL[layer]}")
                text = await generate_architecture_layer(
                    layer=layer,
                    topic=p.topic,
                    genre=p.genre,
                    number_of_chapters=p.target_chapters,
                    word_number=p.target_words_per_chapter,
                    concept=p.concept,
                    global_tendency=p.global_tendency,
                    dna=p.dna,
                    open_ended=p.open_ended,
                    core_seed=(arch.core_seed or "") if arch else "",
                    character_dynamics=(arch.character_dynamics or "") if arch else "",
                    world_building=(arch.world_building or "") if arch else "",
                    directive=(fact_block.strip() or None),
                    intent_block=intent_block,
                )
                save_architecture_layer(session, p, layer, text)
                confirm_architecture_layer(session, p, layer, True)
                session.commit()
                if layer == "character_dynamics":
                    await _extract_personas_safe(session, p)

            # 2) 骨架(全书走向墙):已有且无带话则跳过
            if not p.macro_plan:
                segments = await build_skeleton_segments(
                    session, p, tendency=None, report=report,
                    extra_block=intent_block + fact_block,
                )
                had_outlines = _has_outlines(session, p.id)
                p.macro_plan = segments
                p.skeleton_stale = False
                if had_outlines:
                    p.outline_stale = True
                session.commit()

            # 3) 全书蓝图:已成文的样稿章不重铺(从样稿之后接着铺)
            if sample_n < p.target_chapters:
                report("铺全书章节蓝图")
                await run_blueprint_pipeline(
                    session, p,
                    report=report,
                    start_chapter=sample_n + 1,
                    extra_block=fact_block,
                )
                session.commit()
            finish_job(job_id, {"ok": True, "sample_chapters": sample_n})
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return job_id


@router.post("/{project_id}/full-chain")
async def full_chain(project_id: int, db: Session = Depends(get_db)) -> dict:
    """全链:试读确认②之后由作者点「继续写」触发,后台把整本书铺出来。"""
    project = _get_project_or_404(db, project_id)
    if not project.brief_confirmed:
        raise HTTPException(status_code=409, detail="还没确认理解卡与开书订单")
    return {"job_id": spawn_full_chain(project_id)}
