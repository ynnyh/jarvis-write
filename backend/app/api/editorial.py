# app/api/editorial.py
# -*- coding: utf-8 -*-
"""编辑部接口:主编评分 / 校对 / 审核报告 / 优化动作目录。

GET  /api/editorial/actions                          预设优化动作(正文/大纲两级,配置文件驱动)
POST /api/projects/{id}/chapters/{n}/review-async    主编评分(四维+短评+3条建议)
POST /api/projects/{id}/chapters/{n}/proofread-async 校对(错别字/语病/标点/重复,问题清单)
POST /api/projects/{id}/chapters/{n}/proofread-apply 应用勾选的校对修复(逐条精确替换)
GET  /api/projects/{id}/audit-report                 审核报告(聚合失配章/伏笔/退场人物,零 LLM)
POST /api/projects/{id}/diag-async                   全书体检(LLM 逐章扫矛盾,问题落各章清单)
POST /api/projects/{id}/rule-scan-async              规则扫描(对照世界观硬规则钉板逐章体检)
POST /api/projects/{id}/contracts/backfill-async     老书批量补提章末契约(缺契约章逐章重提)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Foreshadowing, Outline
from app.db.session import SessionLocal, get_db
from app.engines.editorial import (
    apply_proofread_fixes,
    judge_passed,
    load_proofread_snapshot,
    load_review_snapshot,
    proofread_chapter,
    review_chapter,
    store_proofread_snapshot,
    store_review_snapshot,
)
from app.engines.diagnosis import (
    backfill_contracts,
    chapters_missing_contract,
    diagnose_book,
    rule_scan_book,
)
from app.jobs import list_running, spawn_job
from app.paths import resource_path

router = APIRouter(tags=["editorial"], dependencies=[Depends(get_current_user)])

# backend/config/editor_actions.json(源码)或 _MEIPASS/config/(冻结),走 resource_path。
_ACTIONS_PATH = resource_path("config/editor_actions.json")


@lru_cache
def _actions() -> dict:
    with open(_ACTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/editorial/actions")
async def editorial_actions() -> dict:
    """预设优化动作目录(前端渲染 chips;prose=正文级,outline=大纲级)。"""
    return _actions()


@router.get("/api/editorial/model-roles")
async def model_roles() -> dict:
    """按「角色」汇报模型分配现状(D7:创作走贵模型,校验/抽取/去味走便宜模型)。

    模型分级此前是按「任务类型」隐式决定的,用户看不到自己实际在花什么钱、
    也看不到写手和审校是不是同一个模型。这里把它显式摊开:

      · writer    创作刀口(草稿/定稿/场景生成)—— 钱该花在这里
      · auditor   判定刀口(主审/一致性/场景验收)—— 与写手分模型才有客观性
      · worker    廉价杂活(摘要/抽取/去味)—— 不该占强档

    同时给出 self_review 标记:写手与审校指向同一套配置时为真,此时主审分数
    是「模型给自己的作文打分」,乐观偏差会被放大一层。不硬改配置(分不分模型
    是用户的成本选择),但要让这件事可见。
    """
    from app.llm.router import Tier, _tier_config, review_is_self_reviewing

    def _brief(cfg: dict, tier: Tier) -> dict:
        return {
            "tier": tier.value,
            "config_id": cfg.get("id"),
            "name": str(cfg.get("name") or cfg.get("interface_format") or "(未配置)"),
            "model": str(cfg.get("model") or ""),
        }

    try:
        writer = _brief(_tier_config(Tier.QUALITY) or {}, Tier.QUALITY)
        auditor = _brief(_tier_config(Tier.REVIEW) or {}, Tier.REVIEW)
        worker = _brief(_tier_config(Tier.FAST) or {}, Tier.FAST)
    except Exception as exc:  # noqa: BLE001 — 配置读取失败不该让前端报错
        return {"available": False, "reason": str(exc)[:150]}

    # 审校档未单独指定时会回落 quality 档:从 id 相同与否即可判断
    auditor_separated = (
        auditor.get("config_id") is not None
        and writer.get("config_id") is not None
        and auditor["config_id"] != writer["config_id"]
    )
    return {
        "available": True,
        "writer": writer,
        "auditor": auditor,
        "worker": worker,
        "auditor_separated": auditor_separated,
        "self_review": review_is_self_reviewing(),
        "advice": (
            ""
            if auditor_separated
            else "审校档与创作档指向同一套配置:主审是在给同一个模型自己的输出打分,"
                 "乐观偏差会被放大。建议在设置页给「审校」单独配一个模型。"
        ),
    }


def _chapter_with_content(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None or not ch.final_content.strip():
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚无定稿正文")
    return ch


# ---------- 主编评分 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/review-async")
async def review_async(project_id: int, n: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"review-{project_id}-"):
        if job["kind"] == f"review-{project_id}-{n}":
            return {"job_id": jid}
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    outline_block = (
        f"标题:{outline.title}\n目的:{outline.chapter_purpose}\n概要:{outline.summary}"
        if outline else "(无蓝图)"
    )
    content = ch.final_content
    threshold = project.review_pass_threshold

    async def work(progress):
        progress(f"主编正在审读第 {n} 章")
        result = await review_chapter(content, outline_block)
        result["chapter_number"] = n
        # 达标与否由后端按项目阈值硬判,不靠模型自报
        result["passed"] = judge_passed(result["scores"], threshold)
        result["threshold"] = threshold
        # 打上来源/时间标记,与回显快照的展示口径一致(前端据此显示「手动审校」)
        result["source"] = "manual"
        result["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        # 结果落库:编辑部下次打开直接回显,不必再点一次「请主编审读」
        _persist_review_snapshot(project_id, n, result, content)
        return result

    return {"job_id": spawn_job(f"review-{project_id}-{n}", work)}


def _persist_review_snapshot(project_id: int, n: int, result: dict, content: str) -> None:
    """手动主审完成后把结果存进章节快照(独立会话;后台任务里请求会话已关闭)。

    content 是本次审读所依据的正文,作为指纹——若审完正文已被改动,回显自动失效。
    落库失败不影响主审结果正常返回给用户。
    """
    session = SessionLocal()
    try:
        ch = (
            session.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch is not None:
            store_review_snapshot(ch, result, "manual", content)
            session.commit()
    except Exception:  # noqa: BLE001 — 快照落库失败不阻塞主审结果
        session.rollback()
    finally:
        session.close()


@router.get("/api/projects/{project_id}/chapters/{n}/review")
async def get_review(project_id: int, n: int, db: Session = Depends(get_db)):
    """回显最近一次主审结果(生成时或手动),前端进编辑部直接展示。

    正文被编辑/润色/重写/回滚后指纹对不上 → review 为 null(不显示过期评分),
    用户可点「请主编审读」重新审。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章不存在")
    return {"review": load_review_snapshot(ch)}


# ---------- 校对 ----------

@router.post("/api/projects/{project_id}/chapters/{n}/proofread-async")
async def proofread_async(project_id: int, n: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    for jid, job in list_running(f"proofread-{project_id}-"):
        if job["kind"] == f"proofread-{project_id}-{n}":
            return {"job_id": jid}
    content = ch.final_content

    async def work(progress):
        progress(f"校对正在逐句检查第 {n} 章")
        result = await proofread_chapter(content)
        result["chapter_number"] = n
        # 待修清单落库:编辑部下次打开直接回显,正文没变就不必再跑一次校对
        _persist_proofread_snapshot(project_id, n, result["issues"], content)
        return result

    return {"job_id": spawn_job(f"proofread-{project_id}-{n}", work)}


def _persist_proofread_snapshot(
    project_id: int, n: int, issues: list[dict], content: str
) -> None:
    """手动校对完成后把待修清单存进章节快照(独立会话;后台任务里请求会话已关闭)。

    content 是本次校对所依据的正文,作为指纹——用户应用修复后正文变动、指纹失配,
    过期清单自动不再回显。落库失败不影响校对结果正常返回给用户。
    """
    session = SessionLocal()
    try:
        ch = (
            session.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch is not None:
            store_proofread_snapshot(ch, issues, "manual", content, fixed=0)
            session.commit()
    except Exception:  # noqa: BLE001 — 快照落库失败不阻塞校对结果
        session.rollback()
    finally:
        session.close()


@router.get("/api/projects/{project_id}/chapters/{n}/proofread")
async def get_proofread(project_id: int, n: int, db: Session = Depends(get_db)):
    """回显最近一次校对结果(生成时自动修复的 / 手动待修的),前端进编辑部直接展示。

    正文被编辑/润色/重写/回滚后指纹对不上 → proofread 为 null(不显示过期清单),
    用户可点「开始校对」重新跑。
    """
    get_project_or_404(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章不存在")
    return {"proofread": load_proofread_snapshot(ch)}


class ProofreadApplyRequest(BaseModel):
    fixes: list[dict] = Field(min_length=1, max_length=20, description="[{original, suggestion}]")


@router.post("/api/projects/{project_id}/chapters/{n}/proofread-apply")
async def proofread_apply(
    project_id: int, n: int, req: ProofreadApplyRequest, db: Session = Depends(get_db)
):
    """应用勾选的校对修复:逐条精确替换首次出现;改前留版本快照。

    返回 applied/failed 清单;正文有实质变化时建议前端随后调 re-extract-async。
    """
    get_project_or_404(db, project_id)
    ch = _chapter_with_content(db, project_id, n)
    content, applied, failed = apply_proofread_fixes(ch.final_content, req.fixes)
    if applied:
        snapshot_chapter(db, ch, source="edited")
        ch.final_content = content
        ch.word_count = len(content)
        db.commit()
    return {
        "applied": applied,
        "failed": failed,
        "word_count": ch.word_count,
        "final_content": ch.final_content,
    }


# ---------- 审核报告(零 LLM,聚合现有数据) ----------

@router.get("/api/projects/{project_id}/audit-report")
async def audit_report(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    written = {c.chapter_number for c in chapters}
    max_written = max(written) if written else 0
    stale = [c.chapter_number for c in chapters if c.is_stale]

    fores = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .order_by(Foreshadowing.chapter_planted)
        .all()
    )
    # 逾期:预期回收章已写过但状态仍未回收
    overdue = [
        {
            "description": f.description,
            "planted": f.chapter_planted,
            "expected": f.expected_payoff_chapter,
            "status": f.status,
        }
        for f in fores
        if f.status in ("planted", "reinforced")
        and f.expected_payoff_chapter is not None
        and f.expected_payoff_chapter <= max_written
    ]
    open_count = sum(1 for f in fores if f.status in ("planted", "reinforced"))
    resolved_count = sum(1 for f in fores if f.status == "paid_off")

    # 伏笔债务(§1.3):活跃数 / 逾期数 / 平均悬空章数。以「已写到第几章」为基准,
    # 因为问的是「相对于已经写出来的部分,作者欠读者多少笔」。
    from app.engines.consistency.foreshadow_agenda import active_cap, book_debt

    debt = book_debt(
        [f for f in fores if f.status in ("planted", "reinforced")], max_written
    )
    debt["cap"] = active_cap(int(project.target_chapters or 0))
    debt["over_capacity"] = debt["active"] > debt["cap"]

    # 大纲已生成但长期没写的章(跳章检查:前面留洞)
    outline_nums = [
        o.chapter_number
        for o in db.query(Outline.chapter_number)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    ]
    holes = [
        num for num in outline_nums if num < max_written and num not in written
    ]

    # 读者认知(§1.4):披露节奏 + 压着的底牌数。确定性派生,零 LLM。
    # 与伏笔债务并列作为「读者体感」的两条量化线:一个管「欠了多少笔没交代」,
    # 一个管「多久没给读者新东西了」。
    from app.engines.consistency.reader_knowledge import (
        build_reader_view,
        disclosure_rhythm,
        is_twist_chapter,
        render_rhythm_note,
    )

    rhythm = disclosure_rhythm(db, project_id, up_to_chapter=max_written)
    latest_outline = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == max_written + 1)
        .first()
    )
    reader_view = build_reader_view(db, project_id, max_written)
    reader = {
        "disclosed_total": rhythm.total,
        "per_chapter": {str(k): v for k, v in sorted(rhythm.per_chapter.items())},
        "dry_runs": [{"start": s, "length": n} for s, n in rhythm.dry_runs],
        "bursts": [{"chapter": c, "count": n} for c, n in rhythm.bursts],
        "held_cards": len(reader_view.held) + len(reader_view.asymmetries),
        "notes": render_rhythm_note(rhythm, max_written),
        "next_is_twist": is_twist_chapter(latest_outline),
    }

    return {
        "written_chapters": len(chapters),
        "target_chapters": project.target_chapters,
        "stale_chapters": stale,
        "holes": holes,
        # 缺有效契约的已成文章(老书):供前端引导「批量补提契约」
        "contracts_missing": chapters_missing_contract(db, project_id),
        "foreshadow": {
            "total": len(fores),
            "open": open_count,
            "resolved": resolved_count,
            "overdue": overdue,
            # 债务面:伏笔「爱埋不爱收」是全行业通病,这里给可量化的账
            "debt": debt,
        },
        "reader": reader,
        # 事实引用追踪(§1.5):每章是在哪些事实的支撑下写出来的。
        # 与 reader/debt 并列作为「设定层可核查性」的第三条线:债务管伏笔,
        # 认知管读者,这一条管「设定有没有真的落进正文」——某条 critical
        # 事实一条引用记录都没有,说明它只存在于圣经里、从没被写进故事。
        "fact_usage": fact_usage_section(db, project_id, chapters),
    }


def fact_usage_section(db: Session, project_id: int, chapters: list) -> dict:
    """事实引用面:零成本聚合,供 /audit-report 展示。

    只报两类有行动价值的东西,不做全量罗列:
      ① 悬空事实:importance=critical 却零引用记录 → 作者写在圣经里、
         从没进过正文的设定(要么补写,要么降级,要么删);
      ② 无据章节:已成文、却一条引用记录都没有的章 → 大概率是「没查圣经
         凭感觉写的」,这类章最容易出设定漂移,值得优先复核。

    ``chapters`` 是已过滤好的「有正文」章列表(调用方已有,不再查一遍)。
    """
    from app.db.models import Fact as _Fact
    from app.db.models import FactUsage as _FactUsage

    referenced_ids = {
        int(r[0])
        for r in db.query(_FactUsage.fact_id)
        .filter(_FactUsage.project_id == project_id)
        .distinct()
        .all()
    }
    criticals = (
        db.query(_Fact)
        .filter(
            _Fact.project_id == project_id,
            _Fact.importance == "critical",
        )
        .all()
    )
    dangling = [
        {"fact_id": f.id, "content": f.content, "from_chapter": f.valid_from}
        for f in criticals
        if f.id not in referenced_ids
    ]

    chapters_with_usage = {
        int(r[0])
        for r in db.query(_FactUsage.chapter_number)
        .filter(_FactUsage.project_id == project_id)
        .distinct()
        .all()
    }
    unsupported = [
        c.chapter_number
        for c in chapters
        if c.chapter_number not in chapters_with_usage
    ]

    return {
        "referenced_facts": len(referenced_ids),
        "critical_total": len(criticals),
        "dangling": dangling,
        "unsupported_chapters": unsupported,
        "chapters_with_usage": len(chapters_with_usage),
    }


# ---------- 全书体检 / 批量补契约(docs/08 §7 P2) ----------


def _book_job_busy(project_id: int) -> str:
    """章节级任务互斥:生成/同步/放行/体检/补契约/桥段扫描任一在跑,返回其阶段文案。"""
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
        + list_running(f"diag-{project_id}")
        + list_running(f"rulescan-{project_id}")
        + list_running(f"contract-backfill-{project_id}")
        + list_running(f"motifscan-{project_id}")
    )
    return busy[0][1]["stage"] if busy else ""


@router.post("/api/projects/{project_id}/diag-async")
async def diag_async(project_id: int, db: Session = Depends(get_db)):
    """全书体检:LLM 逐章扫描跨章矛盾,问题以「诊断」来源落各章问题清单(幂等重建)。"""
    get_project_or_404(db, project_id)
    if busy := _book_job_busy(project_id):
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await diagnose_book(session, project_id, progress=progress)
        finally:
            session.close()

    return {"job_id": spawn_job(f"diag-{project_id}", work)}


@router.post("/api/projects/{project_id}/contracts/backfill-async")
async def contracts_backfill_async(project_id: int, db: Session = Depends(get_db)):
    """老书批量补契约:缺有效契约的已成文章逐章重提(已有有效契约的跳过)。"""
    get_project_or_404(db, project_id)
    if busy := _book_job_busy(project_id):
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await backfill_contracts(session, project_id, progress=progress)
        finally:
            session.close()

    return {"job_id": spawn_job(f"contract-backfill-{project_id}", work)}


@router.post("/api/projects/{project_id}/rule-scan-async")
async def rule_scan_async(project_id: int, db: Session = Depends(get_db)):
    """规则扫描:LLM 逐章对照「世界观硬规则」体检正文,问题以「规则」来源落各章清单。

    需先在项目里填 world_rules(审核报告页的「世界观硬规则」编辑框)。
    """
    project = get_project_or_404(db, project_id)
    if not (project.world_rules or "").strip():
        raise HTTPException(
            status_code=400, detail="尚未设置世界观硬规则,请先填写再扫描。"
        )
    if busy := _book_job_busy(project_id):
        raise HTTPException(status_code=409, detail=f"已有章节任务在进行中({busy}),稍后再试。")

    async def work(progress) -> dict:
        session = SessionLocal()
        try:
            return await rule_scan_book(session, project_id, progress=progress)
        finally:
            session.close()

    return {"job_id": spawn_job(f"rulescan-{project_id}", work)}
