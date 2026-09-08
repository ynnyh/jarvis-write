"""欠费(402)场景:错误识别、话术、评测断点续跑。

背景(docs/12):50 章压测跑到第 38 章后 DeepSeek 余额耗尽(402),
剩 12 章失败。此前 402 落进通用 HTTP 错误分支,话术误导用户去查 Base URL,
且评测底座没有断点续跑——充值后只能整轮重跑(再烧一遍已成功 37 章的钱)。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


# ---------- LLM 层:402 识别与话术 ----------

def test_check_upstream_402_gives_actionable_message():
    from app.llm.base import UpstreamError, check_upstream

    resp = httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})
    with pytest.raises(UpstreamError) as ei:
        check_upstream(resp)
    assert "余额不足" in str(ei.value)
    assert "充值" in str(ei.value)
    assert ei.value.status == 402
    assert ei.value.retryable is False  # 重试无意义,别烧用户时间


def test_check_upstream_402_not_misleading_base_url_hint():
    """openai_compatible 传的 hint 是「确认 Base URL 含 /v1」,欠费时绝不能带出来。"""
    from app.llm.base import UpstreamError, check_upstream

    resp = httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})
    with pytest.raises(UpstreamError) as ei:
        check_upstream(resp, hint="确认 Base URL 含 /v1 且渠道支持 OpenAI 协议")
    assert "Base URL" not in str(ei.value)


def test_is_insufficient_balance_variants():
    from app.llm.base import UpstreamError, is_insufficient_balance

    assert is_insufficient_balance(UpstreamError("x", status=402))
    assert is_insufficient_balance(UpstreamError("上游返回 HTTP 402: Insufficient Balance"))
    assert is_insufficient_balance(RuntimeError("账户余额不足"))
    assert is_insufficient_balance(RuntimeError("error: Insufficient Balance"))
    assert not is_insufficient_balance(UpstreamError("x", status=500))
    assert not is_insufficient_balance(UpstreamError("HTTP 404 模型不存在"))
    assert not is_insufficient_balance(RuntimeError("超时"))


# ---------- 评测底座:resume_run 断点续跑 ----------

@pytest.fixture()
def run_db(tmp_path: Path):
    """一个带 project + 3 章正文的 run 库 + 对应 run dict(第 3 章 ok=False)。"""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'run.db').as_posix()}"
    import importlib

    import app.db.session as session_mod

    importlib.reload(session_mod)
    from app.db.base import Base
    from app.db.models import Chapter, Outline, Project
    from app.db.session import SessionLocal

    Base.metadata.create_all(bind=session_mod.engine)
    with SessionLocal() as db:
        proj = Project(title="[评测] 续跑测试", target_words_per_chapter=100)
        db.add(proj)
        db.flush()
        for n in (1, 2, 3):
            db.add(
                Outline(
                    project_id=proj.id,
                    chapter_number=n,
                    title=f"第{n}章",
                    summary="摘要",
                    current_version=1,
                )
            )
            if n < 3:
                db.add(
                    Chapter(
                        project_id=proj.id,
                        chapter_number=n,
                        final_content=f"第{n}章正文内容。",
                        word_count=8,
                        status="pending_review",
                    )
                )
        db.commit()
        pid = proj.id
    run = {
        "schema": 1,
        "label": "resume-test",
        "fixture": "demo",
        "fixture_title": "续跑测试",
        "project_id": pid,
        "project_settings": {"target_words": 100},
        "usage": {"calls": 2, "prompt_tokens": 1000, "completion_tokens": 200},
        "chapters": [
            {"n": 1, "title": "第1章", "ok": True, "chars": 8, "seconds": 1.0,
             "status": "pending_review", "quarantined": False, "target_ratio": 0.08,
             "paragraphs": 1, "dialogue_ratio": 0, "within_repeats": 0,
             "flavor": {"score": 2}, "review": {"scores": {"plot": 8}, "passed": True,
             "revision_rounds": 0, "repair_rounds": 0, "comment": ""},
             "gate": {"blocker": 0, "major": 0, "minor": 0},
             "preflight_warnings": 0, "guard_action": "none", "extraction": {}},
            {"n": 2, "title": "第2章", "ok": True, "chars": 8, "seconds": 1.0,
             "status": "pending_review", "quarantined": False, "target_ratio": 0.08,
             "paragraphs": 1, "dialogue_ratio": 0, "within_repeats": 0,
             "flavor": {"score": 2}, "review": {"scores": {"plot": 8}, "passed": True,
             "revision_rounds": 0, "repair_rounds": 0, "comment": ""},
             "gate": {"blocker": 0, "major": 0, "minor": 0},
             "preflight_warnings": 0, "guard_action": "none", "extraction": {}},
            {"n": 3, "title": "第3章", "ok": False, "seconds": 5.0,
             "error": "UpstreamError: HTTP 402: Insufficient Balance"},
        ],
    }
    yield run
    # 还原会话模块,避免污染其他测试
    importlib.reload(session_mod)


@pytest.mark.usefixtures("run_db")
def test_resume_run_recovers_failed_chapters(run_db):
    import asyncio

    import app.evals.runner as runner_mod
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    run = run_db
    pid = run["project_id"]

    async def fake_generate(db, project, n, *a, **k):
        text = f"第{n}章续跑生成的正文内容。"
        ch = (
            db.query(Chapter)
            .filter_by(project_id=project.id, chapter_number=n)
            .first()
        )
        if ch is None:
            ch = Chapter(project_id=project.id, chapter_number=n)
            db.add(ch)
        ch.final_content = text
        ch.word_count = len(text)
        ch.status = "pending_review"
        db.flush()
        chapter = ch
        gate_issues: list = []
        extraction: dict = {}
        guard = type("G", (), {"action": "none"})()
        review = {"scores": {"plot": 8, "prose": 8}, "passed": True,
                  "revision_rounds": 0, "repair_rounds": 0, "comment": ""}
        return chapter, gate_issues, extraction, guard, review, []

    orig = runner_mod.generate_chapter
    runner_mod.generate_chapter = fake_generate
    try:
        resumed = asyncio.run(runner_mod.resume_run(run, progress=lambda s: None))
    finally:
        runner_mod.generate_chapter = orig

    recs = {c["n"]: c for c in resumed["chapters"]}
    assert all(recs[n]["ok"] for n in (1, 2, 3)), "失败章应被续跑恢复"
    assert recs[3]["chars"] > 0
    # 成功章原样保留(不被重算覆盖)
    assert recs[1]["chars"] == 8
    # 用量增量计入
    assert resumed["usage"]["calls"] >= 2
    # 续跑记录落档
    assert resumed["resumes"][-1]["attempted"] == [3]
    assert resumed["resumes"][-1]["recovered"] == [3]
    # 聚合重算:3/3 成功
    assert resumed["aggregate"]["chapters_ok"] == 3
    # 圣经/跨章指标重算不报错
    assert "cross" in resumed
    with SessionLocal() as db:
        ch3 = (
            db.query(Chapter)
            .filter_by(project_id=pid, chapter_number=3)
            .first()
        )
        assert ch3 is not None and ch3.final_content


def test_resume_run_no_failures_is_noop(run_db):
    import asyncio

    import app.evals.runner as runner_mod

    run = run_db
    run["chapters"] = [c for c in run["chapters"] if c.get("ok")]
    out = asyncio.run(runner_mod.resume_run(run, progress=lambda s: None))
    assert "resumes" not in out
