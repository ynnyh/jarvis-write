# tests/test_live_stream.py
# -*- coding: utf-8 -*-
"""实时正文总线:后台任务里模型正在吐的字,能原样流到前端。

覆盖三层:
1. 内存总线本身(app/live.py):归属、裁尾、换屏、收尾、落后重置、内存上限;
2. 唯一的钩子(app/llm/base.py::_iter_live / _once_live):流式与非流式两条路
   都进总线——这是"全链路不漏"的前提,60+ 处 LLM 调用都靠它;
3. HTTP 端点(GET /api/jobs/{id}/live):归属校验 + SSE 帧格式。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import jobs, live
from app.llm.base import LLMAdapter, LLMResponse
from app.main import app

INVITE = "test-invite"


@pytest.fixture(autouse=True)
def _clean_live():
    """每个用例都从干净的总线开始(内存态,跨用例会串)。"""
    live.reset()
    live.current_job_id.set(None)
    yield
    live.reset()


# ---------- 1. 总线本身 ----------

def test_publish_without_job_context_is_dropped():
    """前台请求(无 job 上下文)不占内存:那些路径要么自己有 SSE,要么没人看。"""
    live.publish("前台的字")
    assert live.snapshot("anything") is None


def test_publish_accumulates_and_keeps_only_tail():
    """直播只留尾部:seq 记总字数,缓冲区不超过 _TAIL_CHARS。"""
    live.publish("甲" * 3000, job_id="j1")
    live.publish("乙" * 3000, job_id="j1")
    snap = live.snapshot("j1")
    assert snap["seq"] == 6000                     # 累计字数不因裁尾而少算
    assert len(snap["text"]) <= live._TAIL_CHARS
    assert snap["text"].endswith("乙")             # 留的是最新的那段


def test_single_oversized_chunk_is_trimmed():
    """一次性推来一大段(非流式兜底路径)也要裁,不能靠"至少留一块"把上限撑破。"""
    live.publish("丙" * 12000, job_id="j2")
    snap = live.snapshot("j2")
    assert len(snap["text"]) == live._TAIL_CHARS
    assert snap["seq"] == 12000


def test_set_step_clears_screen_but_seq_stays_monotonic():
    """步骤一变就换屏(清缓冲 + epoch+1),但 seq 不回退——否则订阅游标会糊账。"""
    live.publish("上一步的正文", job_id="j3")
    live.set_step("j3", "正在写草稿")
    snap = live.snapshot("j3")
    assert snap["text"] == ""
    assert snap["step"] == "正在写草稿"
    assert snap["epoch"] == 1
    assert snap["seq"] == 6
    live.set_step("j3", "正在写草稿")              # 同一步重复设不换屏
    assert live.snapshot("j3")["epoch"] == 1


def test_set_step_mid_call_only_relabels():
    """同一次流式调用里更新进度文案(蓝图「已生成 N/M 章」)不能清屏。

    否则用户眼前正在长出来的字每隔几百字消失一次——这是最刺眼的直播 bug。
    """
    live.begin_call(job_id="j5")
    live.publish("第一章开头……", job_id="j5")
    live.set_step("j5", "已生成 1/40 章")
    snap = live.snapshot("j5")
    assert snap["text"] == "第一章开头……"          # 正文照旧往下滚
    assert snap["step"] == "已生成 1/40 章"          # 标签换了
    assert snap["epoch"] == 0                        # 没换屏

    # 这次调用吐完了 → 下一个步骤是真的换步骤,该清屏
    live.end_call(job_id="j5")
    live.set_step("j5", "正在自检")
    snap = live.snapshot("j5")
    assert snap["text"] == ""
    assert snap["epoch"] == 1


def test_concurrent_calls_keep_screen_until_last_one_ends():
    """并发多路调用(如灵感的并发精筛):先收工的那路不能替别人关窗口。"""
    live.begin_call(job_id="j6")
    live.begin_call(job_id="j6")
    live.publish("两路一起写的字", job_id="j6")
    live.end_call(job_id="j6")                       # 第一路结束
    live.set_step("j6", "已确认 1/2 条")
    assert live.snapshot("j6")["text"] == "两路一起写的字"
    live.end_call(job_id="j6")                       # 最后一路也结束
    live.set_step("j6", "正在汇总")
    assert live.snapshot("j6")["text"] == ""


def test_publish_after_close_is_ignored():
    """任务收尾后迟到的增量不再入流(否则已完成任务的直播会诈尸)。"""
    live.publish("正文", job_id="j4")
    live.close("j4")
    live.publish("迟到的字", job_id="j4")
    snap = live.snapshot("j4")
    assert snap["closed"] is True
    assert snap["text"] == "正文"


def test_stream_count_is_bounded():
    """内存兜底:流的数量有上限,已结束的先被清掉。"""
    for i in range(live._MAX_STREAMS + 20):
        jid = f"bulk-{i}"
        live.publish("字", job_id=jid)
        live.close(jid)
    live.publish("字", job_id="fresh")
    assert len(live._STREAMS) <= live._MAX_STREAMS + 1
    assert live.snapshot("fresh") is not None


# ---------- 2. follow():订阅协议 ----------

async def _read_until_done(job_id: str, *, cursor: int = 0, timeout: float = 5.0):
    frames: list[tuple[str, dict]] = []

    async def reader():
        async for frame in live.follow(job_id, cursor=cursor):
            frames.append(frame)
            if frame[0] == "done":
                return

    await asyncio.wait_for(reader(), timeout)
    return frames


def test_follow_gives_snapshot_then_increments_then_done():
    """订阅协议:首帧 step 当快照 → token 增量 → 任务结束发 done。"""

    async def scenario():
        job_id = jobs.create_job("live-follow")
        jobs.update_stage(job_id, "正在写草稿")
        frames: list[tuple[str, dict]] = []

        async def reader():
            async for frame in live.follow(job_id):
                frames.append(frame)
                if frame[0] == "done":
                    return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        live.publish("雪落了", job_id=job_id)
        await asyncio.sleep(0.3)
        live.publish("一夜。", job_id=job_id)
        await asyncio.sleep(0.3)
        jobs.finish_job(job_id, {"ok": True})
        await asyncio.wait_for(task, 5)
        return frames

    frames = asyncio.run(scenario())
    kinds = [k for k, _ in frames]
    assert kinds[0] == "step"
    assert frames[0][1]["step"] == "正在写草稿"
    tokens = [d["text"] for k, d in frames if k == "token"]
    assert "".join(tokens) == "雪落了一夜。"
    assert kinds[-1] == "done"
    assert frames[-1][1]["status"] == "done"
    # 每个内容帧都带 seq,断线重连才能续看
    assert [d["seq"] for k, d in frames if k == "token"] == [3, 6]


def test_follow_resets_when_subscriber_lags_past_buffer():
    """订阅端落后太多(缓冲区已滚过)→ 发 reset 整屏重置,不伪造连续的正文。"""

    async def scenario():
        job_id = jobs.create_job("live-lag")
        live.publish("丁" * (live._TAIL_CHARS + 500), job_id=job_id)
        jobs.finish_job(job_id, None)
        # cursor=1:声称只收到 1 个字,而缓冲区起点远在其后
        return await _read_until_done(job_id, cursor=1)

    frames = asyncio.run(scenario())
    # 首帧是 step(epoch 变化优先),第二次进循环才比较游标
    assert frames[0][0] == "step"
    assert frames[0][1]["text"].endswith("丁")
    assert frames[-1][0] == "done"


def test_follow_reset_frame_reports_dropped_chars():
    """reset 帧要如实报告丢了多少字(epoch 不变的情况下追不上时)。"""

    async def scenario():
        job_id = jobs.create_job("live-drop")
        jobs.update_stage(job_id, "写草稿")
        frames: list[tuple[str, dict]] = []

        async def reader():
            async for frame in live.follow(job_id):
                frames.append(frame)
                if frame[0] == "done":
                    return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)                     # 先拿到 step 快照(seq=0)
        live.publish("戊" * (live._TAIL_CHARS + 300), job_id=job_id)
        await asyncio.sleep(0.3)
        jobs.finish_job(job_id, None)
        await asyncio.wait_for(task, 5)
        return frames

    frames = asyncio.run(scenario())
    resets = [d for k, d in frames if k == "reset"]
    assert resets and resets[0]["dropped"] == 300
    assert len(resets[0]["text"]) == live._TAIL_CHARS


def test_follow_relabels_mid_call_without_new_screen():
    """同一次调用里进度文案变了 → 只发 label 帧,正文接着往下流。"""

    async def scenario():
        job_id = jobs.create_job("live-label")
        jobs.update_stage(job_id, "正在生成蓝图")
        frames: list[tuple[str, dict]] = []

        async def reader():
            async for frame in live.follow(job_id):
                frames.append(frame)
                if frame[0] == "done":
                    return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        live.begin_call(job_id=job_id)
        live.publish("第一章 雪夜", job_id=job_id)
        await asyncio.sleep(0.3)
        jobs.update_stage(job_id, "已生成 1/40 章")     # 调用还在吐字
        await asyncio.sleep(0.3)
        live.publish("第二章 归人", job_id=job_id)
        await asyncio.sleep(0.3)
        live.end_call(job_id=job_id)
        jobs.finish_job(job_id, None)
        await asyncio.wait_for(task, 5)
        return frames

    frames = asyncio.run(scenario())
    kinds = [k for k, _ in frames]
    assert kinds.count("step") == 1                    # 只有首帧那一屏
    assert [d["step"] for k, d in frames if k == "label"] == ["已生成 1/40 章"]
    tokens = "".join(d["text"] for k, d in frames if k == "token")
    assert tokens == "第一章 雪夜第二章 归人"          # 换标签不打断正文


def test_follow_on_unknown_job_ends_immediately():
    """没有这个任务(已被清理)→ 立刻 done,不让订阅者干等。"""
    frames = asyncio.run(_read_until_done("no-such-job"))
    assert frames == [("done", {"status": "gone", "stage": "", "error": None})]


def test_follow_reports_failed_job():
    """任务失败也要发 done,并把错误带上(前端据此收尾提示)。"""

    async def scenario():
        job_id = jobs.create_job("live-fail")
        jobs.fail_job(job_id, "模型连续 3 次返回空正文")
        return await _read_until_done(job_id)

    frames = asyncio.run(scenario())
    assert frames[-1][0] == "done"
    assert frames[-1][1]["status"] == "error"
    assert "空正文" in frames[-1][1]["error"]


# ---------- 3. 钩子:两条 LLM 路径都进总线 ----------

class _StreamAdapter(LLMAdapter):
    """流式假适配器:逐块吐字。"""

    interface_format = "test-stream"

    def __init__(self, **kw):
        super().__init__(api_key="k", model_name="m", **kw)
        self.retry_base_delay = 0

    async def _complete_once(self, messages):
        return LLMResponse(content="非流式整段正文", model="m", finish_reason="stop")

    async def _iter_stream(self, messages, sink):
        for chunk in ("他推开门,", "风雪扑面。"):
            yield chunk
        sink["finish_reason"] = "stop"


def test_llm_stream_inside_job_is_broadcast():
    """后台任务里的流式调用 → 逐块进总线,且归属正确(靠 create_job 设的 ContextVar,
    不需要各接口自己传 job_id——这是"全链路不漏"的关键)。"""

    async def scenario():
        adapter = _StreamAdapter()
        result: dict = {}

        async def work(progress):
            progress("正在写草稿")
            result["text"] = await adapter.ask("写第一章")
            return "done"

        job_id = jobs.spawn_job("live-hook", work)
        for _ in range(500):
            await asyncio.sleep(0.01)
            if jobs.get_job(job_id)["status"] != "running":
                break
        return job_id, result

    job_id, result = asyncio.run(scenario())
    assert result["text"] == "他推开门,风雪扑面。"
    snap = live.snapshot(job_id)
    assert snap is not None, "后台任务的流式增量必须进总线"
    assert snap["text"] == "他推开门,风雪扑面。"
    assert snap["step"] == "正在写草稿"
    assert snap["closed"] is True                  # 任务收尾时关流


def test_llm_non_stream_fallback_is_also_broadcast():
    """渠道不支持流式(回落非流式)时也要播一次:总比一个字都看不到强。"""

    async def scenario():
        adapter = _StreamAdapter()
        adapter.prefer_stream = False               # 直接走非流式那条路
        result: dict = {}

        async def work(progress):
            progress("正在定稿")
            result["text"] = await adapter.ask("定稿")
            return "done"

        job_id = jobs.spawn_job("live-hook-once", work)
        for _ in range(500):
            await asyncio.sleep(0.01)
            if jobs.get_job(job_id)["status"] != "running":
                break
        return job_id, result

    job_id, result = asyncio.run(scenario())
    assert result["text"] == "非流式整段正文"
    assert live.snapshot(job_id)["text"] == "非流式整段正文"


def test_adapter_hook_keeps_screen_while_stage_counts_up():
    """钩子要如实圈出"这一次调用正在吐字"的窗口(begin_call/end_call)。

    蓝图那类环节边写边报「已生成 N/M 章」:窗口内换文案只换标签,窗口外(调用
    结束)换文案才清屏。否则正在长出来的正文会被自己的进度条抹掉。
    """

    async def scenario():
        adapter = _StreamAdapter()
        mid: list[dict] = []

        async def work(progress):
            progress("正在生成蓝图")
            n = 0
            async for _ in adapter.stream(adapter.to_messages("写蓝图")):
                n += 1
                progress(f"已生成 {n}/2 章")          # 同一次调用里反复报进度
                mid.append(live.snapshot(live.current_job_id.get()))
            progress("正在自检")                       # 调用结束后:真的换步骤
            return "done"

        job_id = jobs.spawn_job("live-relabel", work)
        for _ in range(500):
            await asyncio.sleep(0.01)
            if jobs.get_job(job_id)["status"] != "running":
                break
        return job_id, mid

    job_id, mid = asyncio.run(scenario())
    assert [s["epoch"] for s in mid] == [1, 1]         # 进度计数不换屏
    assert mid[-1]["text"] == "他推开门,风雪扑面。"    # 正文没被自己的进度抹掉
    assert mid[-1]["step"] == "已生成 2/2 章"
    final = live.snapshot(job_id)
    assert final["step"] == "正在自检"
    assert final["text"] == ""                         # 真换步骤才清屏
    assert final["epoch"] == 2


def test_foreground_stream_does_not_leak_into_other_jobs():
    """前台流式(直接用 stream() 自己接 SSE 的那几处)无 job 上下文 → 不入流。"""

    async def scenario():
        adapter = _StreamAdapter()
        out = [c async for c in adapter.stream(adapter.to_messages("hi"))]
        return out

    out = asyncio.run(scenario())
    assert "".join(out) == "他推开门,风雪扑面。"    # 照常产出给调用方
    assert live._STREAMS == {}                      # 但不占总线


# ---------- 4. HTTP 端点 ----------

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


def _user_id(username: str) -> int:
    from app.db.models import User
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        return session.query(User).filter(User.username == username).one().id
    finally:
        session.close()


def _finished_job_with_text(username: str, text: str) -> str:
    """造一个属于某用户的、已结束的任务(流里留着最后一屏)。

    已结束是关键:follow 会立刻发完 step+done 收尾,TestClient 才不会一直读流。
    """
    from app.auth import current_user_id

    token = current_user_id.set(_user_id(username))
    try:
        job_id = jobs.create_job("chapter-1-7")
        jobs.update_stage(job_id, "正在写草稿")
        live.publish(text, job_id=job_id)
        jobs.finish_job(job_id, {"ok": True})
        return job_id
    finally:
        current_user_id.reset(token)


def test_live_endpoint_streams_sse_frames(client):
    """本人订阅:拿到 step 快照(带正文)+ done,content-type 是 event-stream。"""
    headers = _auth(client, "live_owner")
    job_id = _finished_job_with_text("live_owner", "雪落了一夜。")

    r = client.get(f"/api/jobs/{job_id}/live", headers=headers)
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers["content-type"]
    assert r.headers["x-accel-buffering"] == "no"    # 反代不许缓冲,否则不是"实时"
    body = r.text
    assert "event: step" in body
    assert "雪落了一夜。" in body
    assert "正在写草稿" in body
    assert "event: done" in body


def test_live_endpoint_rejects_other_users(client):
    """他人订阅 → 404(与 /api/jobs/{id} 同口径:不泄露任务存在性)。"""
    _auth(client, "live_owner2")
    other = _auth(client, "live_peeper")
    job_id = _finished_job_with_text("live_owner2", "机密正文")
    r = client.get(f"/api/jobs/{job_id}/live", headers=other)
    assert r.status_code == 404
    assert "机密正文" not in r.text


def test_live_endpoint_requires_auth(client):
    """未登录 → 401(整个 misc 路由挂了 get_current_user 依赖)。"""
    job_id = _finished_job_with_text("live_owner", "正文")
    assert client.get(f"/api/jobs/{job_id}/live").status_code == 401


def test_live_endpoint_cursor_resumes_without_replay(client):
    """带 cursor 重连:不重放已看过的字(这里 cursor 已到末尾 → 只收快照与收尾)。"""
    headers = _auth(client, "live_resume")
    job_id = _finished_job_with_text("live_resume", "已经看过的正文")
    r = client.get(f"/api/jobs/{job_id}/live?cursor=7", headers=headers)
    assert r.status_code == 200
    assert "event: token" not in r.text     # 换屏快照之后没有多余的增量帧
