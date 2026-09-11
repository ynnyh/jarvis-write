# app/api/chapters/_job.py
# -*- coding: utf-8 -*-
"""章节生成任务体的唯一实现(单章生成 / 按一致性问题修订 / 其它回炉路径共用)。

为什么单列:generation.py 与 issues.py 此前各写了一份逐行相同的 runner ——
同调 generate_chapter → 同调 handoff_payload → 同构造一份 18 键载荷喂 finish_job,
连 `"final_content": chapter.final_content` 都逐字重复。两处都在生成主链路上。

这不只是"重复几行"的问题:任务体里含 session 生命周期与失败收敛的顺序敏感步骤
(commit 后再取契约、失败必 rollback),而 §5.2 那一轮已经证明过同类编排的坑——
`_store_end_state` 排在落库之前会静默读到空剧本,全绿套件照样抓不到。
两处复制意味着任何一处修好,另一处还是旧行为。

调用方只声明差异项(倾向 / 修订指令 / 是否回带自己的字段),其余全在这里。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.chapters._common import _flavor_dict, _gate_payload
from app.db.models import Project
from app.db.session import SessionLocal
from app.engines.pipeline.chapter import generate_chapter
from app.engines.pipeline.handoff import handoff_payload
from app.jobs import fail_job, finish_job, normalize_job_error, update_stage


async def run_chapter_job(
    *,
    job_id: str,
    project_id: int,
    chapter_number: int,
    tendency: dict | None = None,
    revision: str = "",
    extra_payload: dict[str, Any] | None = None,
) -> None:
    """在独立 session 里跑一次章节生成,结果(或失败原因)写进 job。

    调用方负责 create_job 与 fire_and_track —— 本函数只跑任务体,故可直接 await 测试。
    extra_payload 是调用方特有的回带字段(如按问题修订的 applied_issue_id),
    在基础载荷之后并入(同名时以 extra 为准)。
    """
    session: Session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        chapter, issues, stats, guard_result, review_result, preflight = (
            await generate_chapter(
                session, project, chapter_number, tendency,
                progress=lambda s: update_stage(job_id, s),
                revision=revision,
            )
        )
        session.commit()
        # 契约必须在 commit 之后取(它读的是刚落库的章末状态)
        handoff = handoff_payload(session, chapter)
        payload: dict[str, Any] = {
            "chapter_number": chapter.chapter_number,
            "word_count": chapter.word_count,
            "status": chapter.status,
            "final_content": chapter.final_content,
            "draft_content": chapter.draft_content,
            "is_stale": chapter.is_stale,
            "outline_version_used": chapter.outline_version_used,
            "consistency_issues": issues,
            "extraction_stats": stats,
            "ai_flavor": _flavor_dict(chapter.final_content),
            "word_guard_action": guard_result.action,
            "split_info": guard_result.split_info,
            "review": review_result,
            "gate": _gate_payload(chapter, issues),
            "preflight": {"warnings": preflight},
            "handoff_contract": handoff["contract"],
            "handoff_extract_status": handoff["status"],
            "handoff_extract_error": handoff["error"],
        }
        if extra_payload:
            payload.update(extra_payload)
        finish_job(job_id, payload)
    except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
        session.rollback()
        fail_job(job_id, normalize_job_error(exc)[:500])
    finally:
        session.close()
