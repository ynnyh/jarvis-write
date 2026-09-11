# tests/test_chapter_extraction.py
# -*- coding: utf-8 -*-
"""章节重抽取 / 契约重提两个异步端点(TestClient + mock 引擎)。

**为什么单测这一层**:`api/chapters/extraction.py` 是全仓覆盖率最低的模块(14%),
而它装的恰好是最不能出错的东西 ——

- **SQLITE_BUSY 五轮重试**:该文件顶部注释解释了为什么要重试(LLM 调用跨多轮,
  用量记账在别的连接提交会让本连接读快照过期,WAL 下不走 busy_timeout);
- **幂等重抽取**:先清旧账再重建,顺序反了会留下脏事实;
- **「重检干净 → 自动放行」**:这条分支会改章节状态(quarantined → pending_review)
  并补走被跳过的章后链路 —— 判错的代价是坏章被放行上线。

改动这里没有测试网,和 §5.2 那轮 `_store_end_state` 排错位置的盲区是同一类。
本文件覆盖两个端点的全部分支,含归属隔离与异常路径。
"""
from __future__ import annotations

import contextlib
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

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


def _create_project(client: TestClient, headers: dict, title: str = "重抽取测试书") -> int:
    r = client.post(
        "/api/projects", headers=headers, json={"title": title, "target_chapters": 5}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_chapter(
    client: TestClient, pid: int, n: int, content: str, status: str = "approved"
) -> int:
    """直接落库一章(走 API 生成太慢且依赖 LLM)。返回 chapter.id。"""
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        ch = Chapter(
            project_id=pid,
            chapter_number=n,
            final_content=content,
            word_count=len(content),
            status=status,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        return ch.id
    finally:
        db.close()


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    """轮询 job 直到非 running(后台 task 跑在 TestClient 的事件循环上,请求即驱动)。"""
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时未完成: {job}"
        time.sleep(0.02)


@contextlib.contextmanager
def _stub_engine(
    *,
    stats=None,
    rebuilt=0,
    issues=None,
    blockers=None,
    handoff=None,
    extract_side_effect=None,
):
    """把两个端点会调到的引擎函数全部换掉,并把关键 mock 交回断言。

    runner 里是**延迟 import**(函数体内 import),所以 patch 必须打在**源模块**上,
    打 `app.api.chapters.extraction.xxx` 是不存在的属性。
    """
    from app.engines import common as common_mod
    from app.engines.consistency import checker as checker_mod, extractor as extractor_mod
    from app.engines.pipeline import chapter as chapter_mod, handoff as handoff_mod
    from app.llm import router as router_mod

    if stats is None:
        stats = {"created": 2}
    if issues is None:
        issues = []
    if blockers is None:
        blockers = []
    if handoff is None:
        handoff = {"status": "ok", "error": "", "contract": {}}

    extract = AsyncMock(return_value=stats)
    if extract_side_effect is not None:
        extract.side_effect = extract_side_effect
    rebuild = AsyncMock(return_value=rebuilt)
    check = AsyncMock(return_value=issues)
    persist = Mock(return_value=None)
    blocker_of = Mock(return_value=blockers)
    tail = AsyncMock(return_value=None)
    style = AsyncMock(return_value=None)
    contract = AsyncMock(return_value=None)
    payload = Mock(return_value=handoff)
    outline = Mock(return_value=None)
    adapter = Mock(return_value=object())

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(extractor_mod, "extract_and_apply", extract))
        stack.enter_context(patch.object(chapter_mod, "rebuild_summaries_after", rebuild))
        stack.enter_context(patch.object(chapter_mod, "_rolling_summary", Mock(return_value="前情")))
        stack.enter_context(patch.object(chapter_mod, "apply_chapter_tail", tail))
        stack.enter_context(patch.object(chapter_mod, "update_style_memo", style))
        stack.enter_context(patch.object(handoff_mod, "extract_handoff_contract", contract))
        stack.enter_context(patch.object(handoff_mod, "handoff_payload", payload))
        stack.enter_context(patch.object(checker_mod, "check_chapter", check))
        stack.enter_context(patch.object(checker_mod, "persist_issues", persist))
        stack.enter_context(patch.object(checker_mod, "blockers_of", blocker_of))
        stack.enter_context(patch.object(common_mod, "get_outline", outline))
        stack.enter_context(patch.object(router_mod, "get_adapter_for", adapter))
        yield {
            "extract": extract,
            "rebuild": rebuild,
            "check": check,
            "persist": persist,
            "blockers_of": blocker_of,
            "apply_tail": tail,
            "update_style": style,
            "handoff_extract": contract,
            "handoff_payload": payload,
        }


def _fast_retry_sleep():
    """重试等待被换成 0 秒 —— 否则「五轮重试」用例要真等 2+4+8+15 秒。

    只 patch extraction 模块里那个 `asyncio` 名字(它在该文件只用于 sleep),
    不动全局 asyncio。
    """
    from app.api.chapters import extraction as extraction_mod

    return patch.object(extraction_mod, "asyncio", Mock(sleep=AsyncMock()))


# ==================== re-extract-async ====================


def test_re_extract_happy_path(client):
    """正常路径:抽取 → 重建下游摘要 → job done,结果带 stats 与 rebuilt。"""
    headers = _auth(client, "reextract_ok")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文内容")

    with _stub_engine(stats={"created": 3, "updated": 1}, rebuilt=2) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/re-extract-async", headers=headers
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["kind"] == f"re-extract-{pid}-1"
    assert job["result"]["extraction_stats"] == {"created": 3, "updated": 1}
    assert job["result"]["summaries_rebuilt"] == 2
    assert m["extract"].await_count == 1
    assert m["rebuild"].await_count == 1


def test_re_extract_reuses_running_job_for_same_chapter(client):
    """同章任务已在跑 → 复用同一 job_id,不重复起。"""
    from app.jobs import create_job, finish_job

    headers = _auth(client, "reextract_reuse")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 2, "正文")

    existing = create_job(f"re-extract-{pid}-2")
    try:
        with _stub_engine():
            r = client.post(
                f"/api/projects/{pid}/chapters/2/re-extract-async", headers=headers
            )
            assert r.status_code == 200, r.text
            assert r.json()["job_id"] == existing
    finally:
        finish_job(existing, {})


def test_re_extract_retries_on_db_lock_then_succeeds(client):
    """遇 SQLITE_BUSY 先重试,第二次成功 —— 这是本文件存在的主要理由。"""
    headers = _auth(client, "reextract_retry")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "正文")

    with _fast_retry_sleep(), _stub_engine(
        extract_side_effect=[RuntimeError("database is locked"), {"created": 1}]
    ) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/re-extract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert m["extract"].await_count == 2  # 第一次锁 → 第二次成
    assert job["result"]["extraction_stats"] == {"created": 1}


def test_re_extract_gives_up_after_max_attempts(client):
    """一直锁 → 跑满 5 次就放弃,任务转失败(不能无限重试占着 job)。"""
    headers = _auth(client, "reextract_exhaust")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "正文")

    with _fast_retry_sleep(), _stub_engine(
        extract_side_effect=RuntimeError("database is locked")
    ) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/re-extract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error", job
    assert m["extract"].await_count == 5
    assert "database is locked" in (job.get("error") or "")


def test_re_extract_does_not_retry_non_lock_error(client):
    """非锁错误不重试:重试只对锁有意义,对别的错误只会拖慢失败。"""
    headers = _auth(client, "reextract_nolock")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "正文")

    with _fast_retry_sleep(), _stub_engine(
        extract_side_effect=ValueError("模型返回不是 JSON")
    ) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/re-extract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error", job
    assert m["extract"].await_count == 1
    assert "模型返回不是 JSON" in (job.get("error") or "")


def test_re_extract_missing_chapter_is_404(client):
    """章节不存在 → 当场 404(而不是起个注定失败的后台任务)。

    此前该端点缺这一步:任务会在后台以 'NoneType' has no attribute
    'final_content' 失败,用户只看到一句「任务失败」。contract 那条路一直有校验。
    """
    headers = _auth(client, "reextract_404")
    pid = _create_project(client, headers)

    r = client.post(f"/api/projects/{pid}/chapters/9/re-extract-async", headers=headers)
    assert r.status_code == 404, r.text


def test_re_extract_project_isolation(client):
    """他人的项目 → 404;项目归属校验在起任务之前。"""
    mine = _auth(client, "reextract_mine")
    other = _auth(client, "reextract_other")
    pid = _create_project(client, mine)
    _seed_chapter(client, pid, 1, "正文")

    r = client.post(
        f"/api/projects/{pid}/chapters/1/re-extract-async", headers=other
    )
    assert r.status_code == 404, r.text


# ==================== contract-reextract-async ====================


def test_contract_reextract_requires_content(client):
    """本章无定稿正文 → 400(没什么可提取的)。"""
    headers = _auth(client, "contract_empty")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "", status="empty")

    r = client.post(
        f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
    )
    assert r.status_code == 400, r.text


def test_contract_reextract_missing_chapter_is_404(client):
    headers = _auth(client, "contract_404")
    pid = _create_project(client, headers)
    r = client.post(
        f"/api/projects/{pid}/chapters/7/contract-reextract-async", headers=headers
    )
    assert r.status_code == 404, r.text


def test_contract_reextract_rejects_when_chapter_job_running(client):
    """有章节任务在跑 → 409(互斥:重提契约会改圣经与摘要,不能与生成并发)。"""
    from app.jobs import create_job, finish_job

    headers = _auth(client, "contract_busy")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "正文")

    busy = create_job(f"chapter-{pid}-1")
    try:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        assert r.status_code == 409, r.text
        assert "已有章节任务在进行中" in r.json()["detail"]
    finally:
        finish_job(busy, {})


def test_contract_reextract_without_prev_chapter(client):
    """第 1 章没有上一章 → 2 步,只提本章契约。"""
    headers = _auth(client, "contract_no_prev")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文有内容")

    with _stub_engine(issues=[{"severity": "minor"}]) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["kind"] == f"contract-{pid}-1"
    assert m["handoff_extract"].await_count == 1  # 只有本章
    assert job["result"]["issues"] == 1
    assert job["result"]["blockers"] == 0


def test_contract_reextract_with_prev_chapter_covers_both(client):
    """有上一章 → 3 步,上一章与本章契约都要重提(只提本章会让下一章衔接错位)。"""
    headers = _auth(client, "contract_with_prev")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文有内容")
    _seed_chapter(client, pid, 2, "第 2 章正文有内容")

    with _stub_engine() as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/2/contract-reextract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert m["handoff_extract"].await_count == 2
    # 上一章先提,本章后提(顺序即契约:本章门禁要对照刚落库的上一章契约)
    first_chapter = m["handoff_extract"].await_args_list[0].args[2]
    second_chapter = m["handoff_extract"].await_args_list[1].args[2]
    assert (first_chapter, second_chapter) == (1, 2)


def test_contract_reextract_auto_releases_clean_quarantined_chapter(client):
    """quarantined + 重检无致命矛盾 → 自动放行:状态回 pending_review 并补走章后链路。"""
    headers = _auth(client, "contract_release")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文", status="quarantined")

    with _stub_engine(issues=[{"severity": "minor"}], blockers=[]) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["auto_released"] is True
    assert m["apply_tail"].await_count == 1   # 补走了被跳过的章后链路
    assert m["update_style"].await_count == 1
    assert m["rebuild"].await_count == 1

    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
            .first()
        )
        assert ch.status == "pending_review"
    finally:
        db.close()


def test_contract_reextract_keeps_quarantine_when_blockers_remain(client):
    """仍有致命矛盾 → 维持 quarantined,只更新清单(绝不自动放行坏章)。"""
    headers = _auth(client, "contract_hold")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文", status="quarantined")

    with _stub_engine(blockers=[{"severity": "blocker", "description": "与设定冲突"}]) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["auto_released"] is False
    assert job["result"]["blockers"] == 1
    assert m["apply_tail"].await_count == 0

    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
            .first()
        )
        assert ch.status == "quarantined"
    finally:
        db.close()


def test_contract_reextract_does_not_release_non_quarantined(client):
    """非 quarantined 的章即便重检干净也不触发自动放行(它本来就没被拦)。"""
    headers = _auth(client, "contract_normal")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文", status="approved")

    with _stub_engine(blockers=[]) as m:
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["auto_released"] is False
    assert m["apply_tail"].await_count == 0


def test_contract_reextract_reuses_running_job_for_same_chapter(client):
    from app.jobs import create_job, finish_job

    headers = _auth(client, "contract_reuse")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文")

    existing = create_job(f"contract-{pid}-1")
    try:
        with _stub_engine():
            r = client.post(
                f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
            )
            assert r.status_code == 200, r.text
            assert r.json()["job_id"] == existing
    finally:
        finish_job(existing, {})


def test_contract_reextract_fails_job_when_engine_raises(client):
    """门禁重检抛错 → 整体回滚、任务转失败(半程成果不留库,状态维持原样)。"""
    headers = _auth(client, "contract_boom")
    pid = _create_project(client, headers)
    _seed_chapter(client, pid, 1, "第 1 章正文", status="quarantined")

    with _stub_engine() as m:
        m["check"].side_effect = ValueError("门禁调用失败")
        r = client.post(
            f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=headers
        )
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error", job
    assert "门禁调用失败" in (job.get("error") or "")
    assert m["apply_tail"].await_count == 0  # 没走到自动放行

    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
            .first()
        )
        assert ch.status == "quarantined"  # 失败不放行
    finally:
        db.close()


def test_contract_reextract_project_isolation(client):
    mine = _auth(client, "contract_mine")
    other = _auth(client, "contract_other")
    pid = _create_project(client, mine)
    _seed_chapter(client, pid, 1, "第 1 章正文")

    r = client.post(
        f"/api/projects/{pid}/chapters/1/contract-reextract-async", headers=other
    )
    assert r.status_code == 404, r.text
