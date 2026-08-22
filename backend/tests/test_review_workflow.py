# tests/test_review_workflow.py
# -*- coding: utf-8 -*-
"""P1 审核工作流测试(mock LLM,无需 API key)。

覆盖 docs/08 §5.5/§5.3 + P1 任务:
- 状态机:生成干净落库 pending_review;approve 端点(幂等/quarantined 400);
  存量 finalized → approved 一次性迁移(user_version 1→2)
- 连写前置:queue_require_approved 严格档(上一章未 approved 即暂停,原因明确)
  与宽松档(默认,仅 quarantined 暂停)
- issues 状态流转:PATCH open → resolved/ignored(单向,非 open 400)
- apply-revision:suggestion 拼修订指令走重写链路(异步 job),发起即标 resolved
- 写前审核 preflight:蓝图 vs 上章契约产出警告(severity 一律 major,落库
  source=preflight,随生成响应透出);只警告不阻断;上一章缺契约→冒可见降级
  警告、第一章→静默跳过;LLM 失败降级
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

CONTRACT = {
    "in_story_time": "第三日 深夜",
    "location": "破庙内",
    "scene_continues": False,
    "characters": [
        {
            "name": "沈墨",
            "location": "破庙内",
            "physical": "左臂刀伤未愈",
            "emotional": "戒备、疲惫",
            "doing": "刚入睡",
            "knows": ["黑衣人来自听雨楼"],
            "unresolved_intent": "明日动身去渡口",
        }
    ],
    "open_threads": ["庙外脚步声未查明"],
    "time_jump_hint": "none",
}
CONTRACT_JSON = json.dumps(CONTRACT, ensure_ascii=False)

CH1_TEXT = "夜深了,沈墨在破庙里睡去。" * 20
CH2_TEXT = "沈墨在破庙里醒来,看着篝火发呆。" * 20

PREFLIGHT_WARNING = {
    "type": "timeline",
    "description": "蓝图写清晨渡口出发,上章契约是深夜刚入睡且无时间跳跃",
    "evidence": "清晨渡口出发",
    "conflicting_fact": "上章契约:第三日 深夜,沈墨 doing=刚入睡,time_jump_hint=none",
    "suggestion": "蓝图开头补一段天亮的交代,或调整为深夜行动",
}
PREFLIGHT_JSON = json.dumps({"warnings": [PREFLIGHT_WARNING]}, ensure_ascii=False)
CLEAN_PREFLIGHT_JSON = '{"warnings": []}'
CLEAN_GATE_JSON = '{"issues": []}'

HIGH = {"plot": 9, "prose": 9, "pacing": 9, "character": 9}


def _make_db(with_contract: bool = True, **project_kwargs):
    """独立内存库:一个项目 + 两章大纲 + 第 1 章正文(approved)与(可选)契约。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="审核工作流测试书", target_chapters=2, target_words_per_chapter=3000,
        **project_kwargs,
    )
    db.add(project)
    db.flush()
    for n, title in ((1, "破庙夜宿"), (2, "渡口清晨")):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=title,
            chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
        ))
    db.flush()
    ch1 = Chapter(
        project_id=project.id, outline_id=1, chapter_number=1,
        final_content=CH1_TEXT, word_count=len(CH1_TEXT), status="approved",
    )
    db.add(ch1)
    db.flush()
    if with_contract:
        db.add(ChapterState(
            chapter_id=ch1.id, contract=CONTRACT_JSON,
            content_hash=content_hash(CH1_TEXT), extract_status="ok",
        ))
    db.commit()
    return db, project, ch1


class _Adapter:
    """固定返回一条回复的假 LLM,记录全部 prompt。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


class _BoomAdapter:
    """调用即抛异常的假 LLM(降级路径用)。"""

    def __init__(self):
        self.calls = 0

    async def ask(self, prompt: str, system=None) -> str:
        self.calls += 1
        raise RuntimeError("上游 502")


# ---------- 写前审核(引擎级) ----------
def test_preflight_warns_on_blueprint_contract_conflict():
    """蓝图 vs 上章契约:产出警告,severity 强制 major,类型钳制。"""
    from app.engines.consistency import preflight as pf_mod

    db, project, _ch1 = _make_db()
    from app.db.models import Outline
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number == 2)
        .first()
    )
    adapter = _Adapter(PREFLIGHT_JSON)
    with patch.object(pf_mod, "get_adapter_for", return_value=adapter):
        warnings = asyncio.run(pf_mod.preflight_chapter(db, project.id, 2, outline))

    assert len(adapter.prompts) == 1
    prompt = adapter.prompts[0]
    assert "章末交接契约" in prompt and "刚入睡" in prompt  # 上章契约注入
    assert "渡口清晨" in prompt  # 本章蓝图注入
    assert len(warnings) == 1
    w = warnings[0]
    assert w["severity"] == "major"  # 一律 major(只警告不阻断)
    assert w["type"] == "timeline"
    assert w["description"] and w["suggestion"]


def test_preflight_normalizes_unknown_type_and_severity():
    """LLM 乱报 severity/type:severity 强制 major,未知类型钳为 state。"""
    from app.engines.consistency.preflight import _normalize_warning

    w = _normalize_warning({
        "severity": "blocker", "type": "worldrule", "description": "x",
    })
    assert w["severity"] == "major"
    assert w["type"] == "state"


def test_preflight_warns_on_missing_contract():
    """上一章有正文却没有效契约(老书未提取/提取失败/指纹失效)→ 冒一条可见降级
    警告(major,不阻断),但不调 LLM——把过去的「静默降级=无锚」暴露出来(#5)。"""
    from app.engines.consistency import preflight as pf_mod

    db, project, _ch1 = _make_db(with_contract=False)
    from app.db.models import Outline
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number == 2)
        .first()
    )
    adapter = _Adapter(PREFLIGHT_JSON)
    with patch.object(pf_mod, "get_adapter_for", return_value=adapter):
        warnings = asyncio.run(pf_mod.preflight_chapter(db, project.id, 2, outline))
    assert adapter.prompts == []  # 契约缺失走早退,不调 LLM
    assert len(warnings) == 1
    w = warnings[0]
    assert w["severity"] == "major"  # 只警告不阻断
    assert "契约" in w["description"]  # 点明契约缺失/连续性校验降级
    assert w["suggestion"]


def test_preflight_skips_first_chapter():
    """第一章本就没有上一章契约 → 静默跳过,返回空,不调 LLM(不是缺失,不该报警)。"""
    from app.engines.consistency import preflight as pf_mod

    db, project, _ch1 = _make_db(with_contract=False)
    from app.db.models import Outline
    outline1 = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number == 1)
        .first()
    )
    adapter = _Adapter(PREFLIGHT_JSON)
    with patch.object(pf_mod, "get_adapter_for", return_value=adapter):
        warnings = asyncio.run(pf_mod.preflight_chapter(db, project.id, 1, outline1))
    assert warnings == []
    assert adapter.prompts == []  # 没调 LLM


def test_preflight_degrades_on_llm_failure():
    """LLM 调用失败 → 告警留痕,返回空,不阻塞生成流程。"""
    from app.engines.consistency import preflight as pf_mod

    db, project, _ch1 = _make_db()
    from app.db.models import Outline
    outline = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number == 2)
        .first()
    )
    adapter = _BoomAdapter()
    with patch.object(pf_mod, "get_adapter_for", return_value=adapter):
        warnings = asyncio.run(pf_mod.preflight_chapter(db, project.id, 2, outline))
    assert warnings == []
    assert adapter.calls == 1


# ---------- 生成主流程集成:preflight 落库 + 透出 + 不阻断;干净落库 pending_review ----------
class _PipelineAdapter:
    """按 prompt 内容回复:草稿/定稿/契约/摘要;记录全部 prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        if "现在开始写" in prompt:
            return "草稿正文。" * 30
        if "修订后的" in prompt:
            return "定稿正文。" * 30
        if "场记" in prompt:
            return CONTRACT_JSON
        return "前情摘要。"


async def _fake_proofread(*a, **k):
    return {"issues": []}


async def _fake_review_high(*a, **k):
    return {"scores": dict(HIGH), "comment": "", "suggestions": []}


async def _fake_check_clean(*a, **k):
    return []


async def _fake_extract(*a, **k):
    return {"facts": 1}


class _ScriptedPreflight:
    """按脚本返回写前警告;记录是否被调用。"""

    def __init__(self, warnings: list[dict]):
        self._warnings = warnings
        self.calls = 0

    async def __call__(self, db, project_id, chapter_number, outline):
        self.calls += 1
        return self._warnings


def _run_generate(db, project, n, preflight_fn):
    """mock LLM 跑一遍 generate_chapter;preflight 由参数注入(脚本化)。"""
    from app.engines.pipeline import chapter as ch_mod

    adapter = _PipelineAdapter()
    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check_clean),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=preflight_fn),
    ):
        result = asyncio.run(ch_mod.generate_chapter(db, project, n))
    return adapter, result


def test_generate_clean_lands_pending_review():
    """门禁通过(干净)→ 落库 pending_review(不再是 finalized)。"""
    db, project, _ch1 = _make_db()
    pf = _ScriptedPreflight([])
    _a, (chapter, _i, _s, _g, _r, preflight) = _run_generate(db, project, 2, pf)
    assert chapter.status == "pending_review"
    assert preflight == []
    assert pf.calls == 1  # 写前审核跑过一次


def test_preflight_warnings_persisted_and_not_blocking():
    """写前警告:落库 source=preflight(open/major)+ 随返回值透出;不阻断生成。"""
    from app.db.models import ChapterIssue, ChapterSummary

    db, project, _ch1 = _make_db()
    warning = {
        "severity": "major", "type": "timeline",
        "description": PREFLIGHT_WARNING["description"],
        "evidence": PREFLIGHT_WARNING["evidence"],
        "conflicting_fact": PREFLIGHT_WARNING["conflicting_fact"],
        "suggestion": PREFLIGHT_WARNING["suggestion"],
    }
    pf = _ScriptedPreflight([warning])
    _a, (chapter, _i, stats, _g, _r, preflight) = _run_generate(db, project, 2, pf)

    # 不阻断:照常落库 pending_review + 走章后抽取/摘要
    assert chapter.status == "pending_review"
    assert stats == {"facts": 1}
    assert db.query(ChapterSummary).filter(
        ChapterSummary.project_id == project.id, ChapterSummary.chapter_number == 2
    ).first() is not None
    # 透出 + 落库
    assert len(preflight) == 1 and preflight[0]["severity"] == "major"
    rows = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == chapter.id, ChapterIssue.source == "preflight"
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "open" and rows[0].severity == "major"
    assert rows[0].issue_type == "timeline"


# ---------- 迁移:存量 finalized → approved(user_version 1→2)----------
def test_migrate_finalized_to_approved(monkeypatch):
    import tempfile

    from sqlalchemy import create_engine, text

    from app import migrate

    tmp = tempfile.mkdtemp(prefix="jw-mig-status-")
    eng = create_engine(f"sqlite:///{tmp}/mig.db")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chapters (id INTEGER PRIMARY KEY, status VARCHAR(20))"
        ))
        conn.execute(text(
            "INSERT INTO chapters (status) VALUES "
            "('finalized'), ('finalized'), ('pending_review'), ('quarantined'), ('stale')"
        ))
        conn.execute(text("PRAGMA user_version = 1"))  # 模拟已跑过 0→1 迁移的存量库

    monkeypatch.setattr(migrate, "engine", eng)
    migrate._migrate_finalized_to_approved()

    with eng.connect() as conn:
        rows = [r[0] for r in conn.execute(text("SELECT status FROM chapters ORDER BY id"))]
        version = conn.execute(text("PRAGMA user_version")).scalar()
    assert rows == ["approved", "approved", "pending_review", "quarantined", "stale"]
    assert version == 2

    # 幂等:迁移后新写入的 finalized(理论上不会再有)不会被二次迁移误伤
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO chapters (status) VALUES ('finalized')"))
    migrate._migrate_finalized_to_approved()
    with eng.connect() as conn:
        tail = conn.execute(
            text("SELECT status FROM chapters ORDER BY id DESC LIMIT 1")
        ).scalar()
    assert tail == "finalized"  # user_version 已 2,跳过


def test_migrate_queue_require_approved_column(monkeypatch):
    import tempfile

    from sqlalchemy import create_engine, inspect, text

    from app import migrate

    tmp = tempfile.mkdtemp(prefix="jw-mig-queue-")
    eng = create_engine(f"sqlite:///{tmp}/mig.db")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO projects (title) VALUES ('老项目')"))

    monkeypatch.setattr(migrate, "engine", eng)
    migrate._add_queue_require_approved_column()
    cols = {c["name"] for c in inspect(eng).get_columns("projects")}
    assert "queue_require_approved" in cols
    with eng.connect() as conn:
        val = conn.execute(text("SELECT queue_require_approved FROM projects")).scalar()
    assert val in (0, False)  # 默认宽松档

    migrate._add_queue_require_approved_column()  # 幂等,不抛异常


# ---------- API 层:approve / issues PATCH / apply-revision / 连写前置 ----------
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时未完成: {job}"
        time.sleep(0.02)


def _seed_book(username: str, client: TestClient, ch1_status: str = "approved"):
    """直接落库:项目 + 两章大纲 + 第 1 章正文/契约。返回 (headers, project_id)。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter, ChapterState, Outline
    from app.engines.editorial import content_hash

    headers = _auth(client, username)
    r = client.post("/api/projects", headers=headers,
                    json={"title": f"审核工作流-{username}", "target_chapters": 2})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    session = SessionLocal()
    try:
        for n, title in ((1, "破庙夜宿"), (2, "渡口清晨")):
            session.add(Outline(
                project_id=pid, chapter_number=n, title=title,
                chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
            ))
        ch1 = Chapter(
            project_id=pid, chapter_number=1, final_content=CH1_TEXT,
            word_count=len(CH1_TEXT), status=ch1_status,
        )
        session.add(ch1)
        session.flush()
        session.add(ChapterState(
            chapter_id=ch1.id, contract=CONTRACT_JSON,
            content_hash=content_hash(CH1_TEXT), extract_status="ok",
        ))
        session.commit()
    finally:
        session.close()
    return headers, pid


def _set_ch2(status: str, pid: int, with_issue: bool = False) -> int:
    """直接落库第 2 章(指定状态);with_issue 时附一条 open 问题,返回 issue_id。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter, ChapterIssue

    session = SessionLocal()
    try:
        ch = Chapter(
            project_id=pid, chapter_number=2, final_content=CH2_TEXT,
            word_count=len(CH2_TEXT), status=status,
        )
        session.add(ch)
        session.flush()
        issue_id = 0
        if with_issue:
            issue = ChapterIssue(
                chapter_id=ch.id, source="gate", severity="blocker",
                issue_type="state",
                description="上章末刚入睡,本章开头却清醒发呆",
                evidence="沈墨在破庙里醒来",
                suggestion="开头补一段时间流逝的交代",
                status="open", content_hash="x" * 16,
            )
            session.add(issue)
            session.flush()
            issue_id = issue.id
        session.commit()
        return issue_id
    finally:
        session.close()


def _chapter_patches(check_fn=_fake_check_clean, preflight_fn=None, extract_fn=_fake_extract):
    """generate_chapter 的 LLM 依赖统一 mock(patch 定义模块,走 API 同样生效)。"""
    from app.engines.pipeline import chapter as ch_mod

    adapter = _PipelineAdapter()
    return adapter, (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=check_fn),
        patch.object(ch_mod, "extract_and_apply", new=extract_fn),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(
            ch_mod, "preflight_chapter",
            new=preflight_fn or _ScriptedPreflight([]),
        ),
    )




# ----- approve 端点 -----
def test_approve_endpoint(client):
    """pending_review → approved;幂等;quarantined 400;无正文 400;不存在 404。"""
    headers, pid = _seed_book("approve_user", client)
    _set_ch2("pending_review", pid)

    r = client.post(f"/api/projects/{pid}/chapters/2/approve", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    # 幂等:重复 approve 仍 200
    r = client.post(f"/api/projects/{pid}/chapters/2/approve", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # quarantined 不可 approve
    headers2, pid2 = _seed_book("approve_user_q", client)
    _set_ch2("quarantined", pid2)
    r = client.post(f"/api/projects/{pid2}/chapters/2/approve", headers=headers2)
    assert r.status_code == 400
    assert "quarantined" in r.json()["detail"]

    # 无正文(empty)不可 approve;不存在的章 404
    headers3, pid3 = _seed_book("approve_user_e", client)
    _set_ch2("empty", pid3)
    r = client.post(f"/api/projects/{pid3}/chapters/2/approve", headers=headers3)
    assert r.status_code == 400
    r = client.post(f"/api/projects/{pid3}/chapters/9/approve", headers=headers3)
    assert r.status_code == 404


# ----- issues PATCH -----
def test_issue_patch_status_transitions(client):
    """open → resolved/ignored;非 open 再流转 400;非法状态 400;不存在 404。"""
    headers, pid = _seed_book("issue_patch_user", client)
    issue_id = _set_ch2("pending_review", pid, with_issue=True)

    # 非法状态值
    r = client.patch(
        f"/api/projects/{pid}/chapters/2/issues/{issue_id}",
        headers=headers, json={"status": "open"},
    )
    assert r.status_code == 400

    # open → resolved
    r = client.patch(
        f"/api/projects/{pid}/chapters/2/issues/{issue_id}",
        headers=headers, json={"status": "resolved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"

    # 单向:已 resolved 不可再流转
    r = client.patch(
        f"/api/projects/{pid}/chapters/2/issues/{issue_id}",
        headers=headers, json={"status": "ignored"},
    )
    assert r.status_code == 400

    # 不存在的问题 404
    r = client.patch(
        f"/api/projects/{pid}/chapters/2/issues/99999",
        headers=headers, json={"status": "ignored"},
    )
    assert r.status_code == 404

    # open → ignored 也合法
    headers2, pid2 = _seed_book("issue_patch_user2", client)
    issue_id2 = _set_ch2("pending_review", pid2, with_issue=True)
    r = client.patch(
        f"/api/projects/{pid2}/chapters/2/issues/{issue_id2}",
        headers=headers2, json={"status": "ignored"},
    )
    assert r.status_code == 200 and r.json()["status"] == "ignored"


# ----- apply-revision -----
def test_apply_revision_endpoint(client):
    """采纳建议:拼修订指令走重写链路(job),发起即标 resolved,重写后门禁重判。"""
    from app.db.session import SessionLocal
    from app.db.models import ChapterIssue
    from app.engines.editorial import content_hash

    headers, pid = _seed_book("apply_rev_user", client)
    issue_id = _set_ch2("pending_review", pid, with_issue=True)
    # 种子 issue 的指纹对齐当前正文(模拟门禁真实落库),否则会被指纹失效语义清除
    session = SessionLocal()
    try:
        session.get(ChapterIssue, issue_id).content_hash = content_hash(CH2_TEXT)
        session.commit()
    finally:
        session.close()

    adapter = _PipelineAdapter()
    # 让门禁检查阻塞在放行事件上:保证"发起即 resolved"的断言发生在重写落库
    # (指纹失效清除旧记录)之前,消除后台 job 竞态。
    import threading
    release = threading.Event()

    async def _blocking_check(*a, **k):
        await asyncio.get_event_loop().run_in_executor(None, release.wait, 10)
        return []

    from app.engines.pipeline import chapter as ch_mod
    patches = (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_blocking_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review_high),
        patch.object(ch_mod, "preflight_chapter", new=_ScriptedPreflight([])),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/2/issues/{issue_id}/apply-revision",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        # 发起即标 resolved(同步落在受理时,不等 job 完成)
        session = SessionLocal()
        try:
            assert session.get(ChapterIssue, issue_id).status == "resolved"
        finally:
            session.close()
        release.set()  # 放行走完重写
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["applied_issue_id"] == issue_id
    assert job["result"]["status"] == "pending_review"  # 重写后门禁干净
    # 重写后正文指纹变化:旧 resolved 记录按指纹失效语义清除,同类问题未消除
    # 会以新的 open 记录回来(本次 mock 门禁干净 → 无 open 遗留)
    session = SessionLocal()
    try:
        assert session.get(ChapterIssue, issue_id) is None
    finally:
        session.close()
    # 修订指令(问题+建议)确实进了重写草稿 prompt
    draft_prompts = [p for p in adapter.prompts if "现在开始写" in p]
    assert draft_prompts and "重写要求" in draft_prompts[0]
    assert "开头补一段时间流逝的交代" in draft_prompts[0]

    # 重写后旧 issue 已被指纹失效语义清除 → 再次发起修订 404
    r = client.post(
        f"/api/projects/{pid}/chapters/2/issues/{issue_id}/apply-revision",
        headers=headers,
    )
    assert r.status_code == 404

    # 非 open(PATCH 标 resolved 后,正文未动指纹仍有效)不能再发起修订 → 400
    headers2, pid2 = _seed_book("apply_rev_user2", client)
    issue_id2 = _set_ch2("pending_review", pid2, with_issue=True)
    r = client.patch(
        f"/api/projects/{pid2}/chapters/2/issues/{issue_id2}",
        headers=headers2, json={"status": "resolved"},
    )
    assert r.status_code == 200
    r = client.post(
        f"/api/projects/{pid2}/chapters/2/issues/{issue_id2}/apply-revision",
        headers=headers2,
    )
    assert r.status_code == 400
    # 不存在的问题 404
    r = client.post(
        f"/api/projects/{pid2}/chapters/2/issues/99999/apply-revision",
        headers=headers2,
    )
    assert r.status_code == 404


# ----- 连写前置:queue_require_approved -----
def test_queue_strict_pauses_when_prev_not_approved(client):
    """严格档:上一章 pending_review(未 approved)→ 队列暂停,原因明确,下一章不生成。"""
    headers, pid = _seed_book("queue_strict_user", client, ch1_status="pending_review")
    # 打开严格连写开关
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"queue_require_approved": True})
    assert r.status_code == 200, r.text
    assert r.json()["queue_require_approved"] is True

    _adapter, patches = _chapter_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/generate-queue",
            headers=headers, json={"chapter_numbers": [2]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error"
    assert "尚未人工审核通过" in job["error"]
    assert "pending_review" in job["error"]
    # 第 2 章未生成
    from app.db.session import SessionLocal
    from app.db.models import Chapter
    session = SessionLocal()
    try:
        ch2 = session.query(Chapter).filter(
            Chapter.project_id == pid, Chapter.chapter_number == 2
        ).first()
        assert ch2 is None
    finally:
        session.close()


def test_queue_strict_passes_when_prev_approved(client):
    """严格档:上一章已 approved → 照常生成。"""
    headers, pid = _seed_book("queue_strict_ok", client, ch1_status="approved")
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"queue_require_approved": True})
    assert r.status_code == 200

    _adapter, patches = _chapter_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/generate-queue",
            headers=headers, json={"chapter_numbers": [2]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["completed"][0]["chapter_number"] == 2


def test_queue_lenient_ignores_prev_pending_review(client):
    """宽松档(默认):上一章 pending_review 不暂停,仅 quarantined 暂停(现状)。"""
    headers, pid = _seed_book("queue_lenient_user", client, ch1_status="pending_review")

    _adapter, patches = _chapter_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/generate-queue",
            headers=headers, json={"chapter_numbers": [2]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["completed"][0]["chapter_number"] == 2


# ----- 生成响应透出 preflight 字段 -----
def test_generate_response_carries_preflight(client):
    """生成响应带 preflight.warnings;章状态 pending_review;gate 字段照旧。"""
    headers, pid = _seed_book("preflight_api_user", client)
    warning = dict(PREFLIGHT_WARNING, severity="major",
                   conflicting_fact=PREFLIGHT_WARNING["conflicting_fact"])
    pf = _ScriptedPreflight([warning])
    _adapter, patches = _chapter_patches(preflight_fn=pf)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post(
            f"/api/projects/{pid}/chapters/2/generate", headers=headers, json={}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["gate"]["status"] == "passed"
    assert len(body["preflight"]["warnings"]) == 1
    assert body["preflight"]["warnings"][0]["severity"] == "major"
    assert body["preflight"]["warnings"][0]["type"] == "timeline"
    # issues 查询端点可见 source=preflight 的 open 记录
    r = client.get(f"/api/projects/{pid}/chapters/2/issues", headers=headers)
    assert r.status_code == 200
    preflight_rows = [i for i in r.json() if i["source"] == "preflight"]
    assert len(preflight_rows) == 1 and preflight_rows[0]["status"] == "open"
