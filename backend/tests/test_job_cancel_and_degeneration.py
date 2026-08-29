# backend/tests/test_job_cancel_and_degeneration.py
# -*- coding: utf-8 -*-
"""手动终止任务 + 复读退化快败:省 token 双护栏。

- 终止:spawn_job 与裸 fire_and_track 两种模式都能被 cancel_running_job 掐断,
  任务落 error(已手动终止);重复取消返回 False;API 层归属校验(404/400)
- 复读快败:finish_reason=length 且正文大量重复 → 不再翻倍重试,直接抛
  UpstreamError(只调 1 次 LLM);正常截断仍走 3 轮重试并返回最长一次
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


def _uid(client: TestClient, headers: dict) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


# ---------- 复读退化检测(纯函数) ----------

DEGENERATE_SENT = "他看着镜子里的自己,看着自己哭。"


def test_looks_degenerate_rules():
    from app.llm.base import _looks_degenerate

    # 同一句连抄 60 遍 → 退化(线上第 4 章的真实形态)
    assert _looks_degenerate(DEGENERATE_SENT * 60) is True
    # 40 句各不相同 → 正常
    normal = "".join(f"第{i}段情节各不相同,人物行动推进主线发展。" for i in range(40))
    assert _looks_degenerate(normal) is False
    # 短文不判(句子总数 < 24):复读率再高也不冤枉
    assert _looks_degenerate(DEGENERATE_SENT * 3) is False


class _FakeAdapter:
    """鸭子类型适配器:按脚本返回响应,记录调用次数(只依赖 complete/max_tokens)。"""

    def __init__(self, contents, finish="length"):
        self._contents = list(contents)
        self.finish = finish
        self.calls = 0
        self.max_tokens = 1024
        self.model_name = "fake-model"

    async def complete(self, messages):
        self.calls += 1
        idx = min(self.calls - 1, len(self._contents) - 1)
        return SimpleNamespace(
            content=self._contents[idx], finish_reason=self.finish, reasoning="",
            model=self.model_name, prompt_tokens=10, completion_tokens=20,
        )


def test_truncated_degenerate_fails_fast_without_retry():
    """截断 + 复读 → 只调 1 次就抛错,不翻倍预算陪跑。"""
    from app.llm.base import UpstreamError, complete_text_with_budget

    adapter = _FakeAdapter([DEGENERATE_SENT * 60], finish="length")
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(complete_text_with_budget(adapter, []))
    assert adapter.calls == 1
    assert "复读退化" in str(exc.value)
    assert adapter.max_tokens == 1024  # finally 恢复原预算,没有翻倍


def test_truncated_normal_still_retries_and_returns_longest():
    """正常内容的截断(非复读)维持原语义:3 轮重试,返回最长的一次。"""
    from app.llm.base import complete_text_with_budget

    normal = "".join(f"第{i}段情节各不相同,人物行动推进主线发展。" for i in range(40))
    adapter = _FakeAdapter([normal, normal, normal], finish="length")
    out = asyncio.run(complete_text_with_budget(adapter, []))
    assert adapter.calls == 3
    assert out == normal


# ---------- 手动终止 ----------

def test_cancel_spawned_job_marks_terminated():
    from app import jobs

    async def main():
        async def work(progress):
            await asyncio.sleep(30)

        jid = jobs.spawn_job("test-cancel-spawn", work)
        await asyncio.sleep(0.1)
        assert jobs.cancel_running_job(jid) is True
        await asyncio.sleep(0.1)
        job = jobs.get_job(jid)
        assert job["status"] == "error"
        assert "终止" in (job["error"] or "")
        # 已结束的任务不可再取消
        assert jobs.cancel_running_job(jid) is False

    asyncio.run(main())


def test_cancel_raw_fire_and_track_marks_failed():
    """裸 fire_and_track 模式(chapter 生成 runner 的形态):runner 不兜
    CancelledError,由 done 回调补记失败,任务不会永远停在 running。"""
    from app import jobs

    async def main():
        jid = jobs.create_job("test-cancel-raw")

        async def runner():
            await asyncio.sleep(30)

        jobs.fire_and_track(runner())
        await asyncio.sleep(0.1)
        assert jobs.cancel_running_job(jid) is True
        await asyncio.sleep(0.1)
        job = jobs.get_job(jid)
        assert job["status"] == "error"
        assert "终止" in (job["error"] or "")

    asyncio.run(main())


# ---------- API 层:归属校验 ----------

def test_cancel_endpoint_unknown_job_404(client):
    headers = _auth(client, "cancel_unknown_user")
    r = client.post("/api/jobs/deadbeef0000/cancel", headers=headers)
    assert r.status_code == 404


def test_cancel_endpoint_owner_and_state_guards(client):
    from app.auth import current_user_id
    from app.jobs import create_job, fail_job

    headers_a = _auth(client, "cancel_owner_user")
    headers_b = _auth(client, "cancel_other_user")

    # A 建一个 running 任务
    tok = current_user_id.set(_uid(client, headers_a))
    try:
        jid = create_job("test-cancel-api")
    finally:
        current_user_id.reset(tok)

    # B 取消 A 的任务 → 按不存在处理(不泄露任务存在性)
    r = client.post(f"/api/jobs/{jid}/cancel", headers=headers_b)
    assert r.status_code == 404

    # A 取消已结束的任务 → 400
    tok = current_user_id.set(_uid(client, headers_a))
    try:
        fail_job(jid, "提前结束")
    finally:
        current_user_id.reset(tok)
    r = client.post(f"/api/jobs/{jid}/cancel", headers=headers_a)
    assert r.status_code == 400


# ---------- 测试模型配置:未知协议兜 400 而不是 500 ----------

def test_provider_test_unknown_format_returns_clean_error(client):
    """历史遗留的非法 interface_format(如 embedding):「测试连接」返回
    ok=False 的可读文案,而不是工厂 ValueError 直接 500。"""
    from app.auth import current_user_id
    from app.crypto import encrypt
    from app.db.models import ProviderConfig
    from app.db.session import SessionLocal

    headers = _auth(client, "provider_bad_fmt_user")
    uid = _uid(client, headers)
    session = SessionLocal()
    try:
        row = ProviderConfig(
            user_id=uid, name="坏配置", interface_format="embedding",
            api_key=encrypt("sk-x"), base_url="https://x/v1", model="m",
        )
        session.add(row)
        session.commit()
        config_id = row.id
    finally:
        session.close()

    r = client.post(f"/api/settings/providers/{config_id}/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "未知 provider" in body["error"]
