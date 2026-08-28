# tests/test_render.py
# -*- coding: utf-8 -*-
"""出片引擎(轻量档)测试:配置、提交、版本采用、归属隔离、级联清理。

平台调用全程打桩:patch `app.api.render.start_render`,假引擎照真引擎的收尾
动作走(标 success → 落一个最小合法 mp4 → apply_pointer 回写指针),所以
控制面(建任务/去重/指针/清理)全链路被验证,而一个网络包都不发。
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app

INVITE = "test-invite"

# 最小合法 MP4:文件头第 4-8 字节是 "ftyp"(save_render_result 就验这一口)
_MINI_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp4a" + b"\x00" * 64


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


def _uid(client: TestClient, username: str) -> int:
    """按用户名查注册后的真实 user id(种数据要挂对主人,归属校验才认)。"""
    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return db.query(User).filter(User.username == username).first().id


def _config_token(client: TestClient, headers: dict, token: str = "ak-test-123456789") -> dict:
    r = client.put(
        "/api/render/config",
        headers=headers,
        json={
            "token": token,
            "resolution": "768p",
            "workflow_i2v": "wf-i2v-test",
            "workflow_t2v": "wf-t2v-test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时: {job}"
        time.sleep(0.02)


class _FakeRender:
    """假引擎:按 spec 走真引擎的收尾(success → 落盘 mp4 → 回写指针)。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.specs: list[dict] = []

    async def __call__(self, progress, spec: dict) -> dict:
        self.specs.append(spec)
        if self.delay:
            await asyncio.sleep(self.delay)
        from app.db.models import RenderTask
        from app.db.session import SessionLocal
        from app.engines.render.service import apply_pointer

        with SessionLocal() as db:
            task = db.get(RenderTask, int(spec["task_id"]))
            assert task is not None
            rel = storage.save_render_result(task.id, _MINI_MP4)
            task.status = "success"
            task.result_path = rel
            db.commit()
        apply_pointer(
            spec["line"], spec.get("shot_id"), spec.get("clip_id"),
            int(spec.get("chunk_index", -1)), rel,
        )
        return {"task_id": spec["task_id"], "status": "success", "result_path": rel}


def _seed_drama_shot(user_id: int, project_id: int, *, with_still: bool = False) -> int:
    """直种一集一分镜(不跑 LLM 管线);with_still 时给格上挂一张本地静帧记录。"""
    from app.db.models import DramaEpisode, DramaShot, DramaStyleCard
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.add(DramaStyleCard(project_id=project_id, style_name="测试画风", ratio="9:16", negative="模糊"))
        ep = DramaEpisode(project_id=project_id, ep_index=1, title="第一集")
        db.add(ep)
        db.flush()
        shot = DramaShot(
            episode_id=ep.id,
            seq=1,
            prompt_cn="少女在雨夜便利店门口回头",
            camera="推",
            duration_s=20,  # 故意超上限,验证夹到 15
            motion_cn="镜头缓慢推近,她抬起头",
            assets=(
                [{"kind": "upload", "src": "drama/x/shot999-1.png", "note": ""}]
                if with_still
                else []
            ),
        )
        db.add(shot)
        db.commit()
        return shot.id


def _seed_mood_clip(user_id: int, *, with_ref: bool = False) -> int:
    """直种一条短片(手卡 + 切段);with_ref 时给 0 号段挂一张参考图记录。"""
    from app.db.models import ClipShoot, MoodClip
    from app.db.session import SessionLocal

    clip_payload = {
        "shots": [
            {"seq": 1, "camera": "固定", "action_desc": "她站在天台上", "prompt_cn": "天台少女,黄昏", "duration_s": 4},
            {"seq": 2, "camera": "推", "action_desc": "风吹起她的头发", "prompt_cn": "风吹头发特写", "duration_s": 3},
        ],
        "chunks": [
            {"index": 0, "start_s": 0, "end_s": 7, "duration_s": 7, "shot_seqs": [1, 2], "subtitle": "…"},
            {"index": 1, "start_s": 7, "end_s": 15, "duration_s": 8, "shot_seqs": [3], "subtitle": "…"},
        ],
    }
    with SessionLocal() as db:
        row = MoodClip(
            user_id=user_id, theme=" regret", duration_s=15, direction="anime",
            negative="过度饱和", chosen=0, clip=clip_payload, status="picked",
        )
        db.add(row)
        db.flush()
        shoot = ClipShoot(
            user_id=user_id,
            clip_id=row.id,
            shoot=[
                {
                    "index": 0, "start_s": 0, "end_s": 7, "duration_s": 7,
                    "over_limit": False, "subtitle": "…", "shot_seqs": [1, 2], "scenes": [],
                    "ref_images": (
                        [{"kind": "upload", "src": "clips/x/0-1.png", "note": ""}]
                        if with_ref
                        else []
                    ),
                    "done": False, "result_link": "", "note": "",
                },
                {
                    "index": 1, "start_s": 7, "end_s": 15, "duration_s": 8,
                    "over_limit": False, "subtitle": "…", "shot_seqs": [3], "scenes": [],
                    "ref_images": [], "done": False, "result_link": "", "note": "",
                },
            ],
        )
        db.add(shoot)
        db.commit()
        return row.id


# =============== 配置 ===============


def test_render_config_roundtrip(client: TestClient):
    headers = _auth(client, "render_cfg_a")
    r = client.get("/api/render/config", headers=headers)
    assert r.status_code == 200 and r.json()["configured"] is False

    out = _config_token(client, headers)
    assert out["configured"] is True and out["has_token"] is True
    assert "ak-test" not in out["token_masked"] and "*" in out["token_masked"]

    # token 留空 = 不改动;workflow 留空 = 保持
    r = client.put(
        "/api/render/config", headers=headers,
        json={"token": "", "resolution": "480p", "workflow_i2v": "", "workflow_t2v": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_token"] is True and body["resolution"] == "480p"
    assert body["workflow_i2v"] == "wf-i2v-test"

    # 非法分辨率 / 内网 base_url(SSRF)都要拒
    r = client.put("/api/render/config", headers=headers, json={"token": "", "resolution": "1080p"})
    assert r.status_code == 400
    r = client.put(
        "/api/render/config", headers=headers,
        json={"token": "", "base_url": "http://127.0.0.1:9000"},
    )
    assert r.status_code == 400


def test_submit_requires_token(client: TestClient):
    username = "render_cfg_b"
    headers = _auth(client, username)
    r = client.post("/api/projects", headers=headers, json={"title": "出片书"})
    pid = r.json()["id"]
    sid = _seed_drama_shot(_uid(client, username), pid)
    r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
    assert r.status_code == 400
    assert "设置" in r.json()["detail"]


# =============== 漫剧:提交 → 指针 → 采用 ===============


def test_drama_render_flow(client: TestClient):
    username = "render_flow_a"
    headers = _auth(client, username)
    pid = client.post("/api/projects", headers=headers, json={"title": "出片书A"}).json()["id"]
    sid = _seed_drama_shot(_uid(client, username), pid, with_still=True)
    _config_token(client, headers)

    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r.status_code == 200, r.text
        job_id, task_id = r.json()["job_id"], r.json()["task_id"]
        job = _wait_job(client, headers, job_id)
        assert job["status"] == "done", job

    spec = fake.specs[0]
    assert spec["kind"] == "i2v" and spec["workflow_id"] == "wf-i2v-test"
    assert spec["first_frame"]["kind"] == "upload"
    assert spec["params"]["duration_s"] == 15  # 20s 夹到工作流上限
    assert "768p竖" == spec["params"]["resolution"]  # 画风卡 9:16
    assert "镜头缓慢推近" in spec["params"]["prompt"]
    assert "模糊" in spec["params"]["prompt"]  # 风格卡负面词并入

    r = client.get(f"/api/projects/{pid}/drama/shots/{sid}/render/tasks", headers=headers)
    tasks = r.json()["tasks"]
    assert tasks and tasks[0]["status"] == "success"
    rel = tasks[0]["result_path"]

    # 引擎自动回写指针;读文件走鉴权端点是合法 mp4
    from app.db.models import DramaShot
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert db.get(DramaShot, sid).clip_ref == rel
    r = client.get(f"/api/render/tasks/{task_id}/file", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"

    # 同一格再出一版(重 roll):攒两版,adopt 可切回旧版
    with patch("app.api.render.start_render", _FakeRender()):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        job2 = _wait_job(client, headers, r.json()["job_id"])
        assert job2["status"] == "done"
    tasks = client.get(f"/api/projects/{pid}/drama/shots/{sid}/render/tasks", headers=headers).json()["tasks"]
    assert len(tasks) == 2
    old_id = tasks[1]["id"]
    r = client.post(f"/api/render/tasks/{old_id}/adopt", headers=headers)
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.get(DramaShot, sid).clip_ref == tasks[1]["result_path"]

    # queued/failed 的版本不能采用
    r = client.post("/api/render/tasks/999999/adopt", headers=headers)
    assert r.status_code == 404


def test_drama_render_t2v_without_still(client: TestClient):
    username = "render_flow_b"
    headers = _auth(client, username)
    pid = client.post("/api/projects", headers=headers, json={"title": "出片书B"}).json()["id"]
    sid = _seed_drama_shot(_uid(client, username), pid, with_still=False)
    _config_token(client, headers)

    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r.status_code == 200, r.text
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert fake.specs[0]["kind"] == "t2v"
    assert fake.specs[0]["workflow_id"] == "wf-t2v-test"
    assert fake.specs[0]["first_frame"] is None


def test_render_dedup_running(client: TestClient):
    username = "render_dedup"
    headers = _auth(client, username)
    pid = client.post("/api/projects", headers=headers, json={"title": "出片书C"}).json()["id"]
    sid = _seed_drama_shot(_uid(client, username), pid, with_still=True)
    _config_token(client, headers)

    with patch("app.api.render.start_render", _FakeRender(delay=1.5)):
        r1 = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r1.status_code == 200 and r1.json()["deduped"] is False
        r2 = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r2.status_code == 200 and r2.json()["deduped"] is True
        assert r2.json()["job_id"] == r1.json()["job_id"]
        _wait_job(client, headers, r1.json()["job_id"], timeout=60)


# =============== 情绪短片:段级提交 ===============


def test_clips_render_flow(client: TestClient):
    username = "render_clips_a"
    headers = _auth(client, username)
    clip_id = _seed_mood_clip(_uid(client, username), with_ref=True)
    _config_token(client, headers)

    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/clips/{clip_id}/shoot/0/render", headers=headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        assert _wait_job(client, headers, job_id)["status"] == "done"

    spec = fake.specs[0]
    assert spec["line"] == "clips" and spec["kind"] == "i2v"
    # 7 秒段;提示词带两格的运动句与风格卡负面词
    assert spec["params"]["duration_s"] == 7
    assert "她站在天台上" in spec["params"]["prompt"]
    assert "风吹起她的头发" in spec["params"]["prompt"]
    assert "过度饱和" in spec["params"]["prompt"]

    tasks = client.get(f"/api/clips/{clip_id}/shoot/0/render/tasks", headers=headers).json()["tasks"]
    assert tasks and tasks[0]["status"] == "success"
    from app.db.models import ClipShoot
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.query(ClipShoot).filter(ClipShoot.clip_id == clip_id).first()
        assert row.shoot[0]["result_link"] == tasks[0]["result_path"]
        assert row.shoot[1]["result_link"] == ""

    # 越界段 404
    r = client.post(f"/api/clips/{clip_id}/shoot/9/render", headers=headers)
    assert r.status_code == 404


def test_clips_render_t2v_without_ref(client: TestClient):
    username = "render_clips_b"
    headers = _auth(client, username)
    clip_id = _seed_mood_clip(_uid(client, username), with_ref=False)
    _config_token(client, headers)
    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/clips/{clip_id}/shoot/1/render", headers=headers)
        assert r.status_code == 200, r.text
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert fake.specs[0]["kind"] == "t2v"


# =============== 归属隔离 ===============


def test_render_ownership_isolation(client: TestClient):
    ua, ub = "render_own_a", "render_own_b"
    ha = _auth(client, ua)
    hb = _auth(client, ub)
    pid = client.post("/api/projects", headers=ha, json={"title": "A的书"}).json()["id"]
    sid = _seed_drama_shot(_uid(client, ua), pid, with_still=True)
    clip_id = _seed_mood_clip(_uid(client, ua), with_ref=True)
    _config_token(client, ha)

    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=ha)
        task_id = r.json()["task_id"]
        _wait_job(client, ha, r.json()["job_id"])
        r2 = client.post(f"/api/clips/{clip_id}/shoot/0/render", headers=ha)
        clip_task_id = r2.json()["task_id"]
        _wait_job(client, ha, r2.json()["job_id"])

    # B 对 A 的项目/短片提交 → 404;对 A 的任务读取/采用/取文件 → 404
    assert client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=hb).status_code == 404
    assert client.post(f"/api/clips/{clip_id}/shoot/0/render", headers=hb).status_code == 404
    assert client.get(f"/api/projects/{pid}/drama/shots/{sid}/render/tasks", headers=hb).status_code == 404
    assert client.get(f"/api/clips/{clip_id}/shoot/0/render/tasks", headers=hb).status_code == 404
    assert client.post(f"/api/render/tasks/{task_id}/adopt", headers=hb).status_code == 404
    assert client.get(f"/api/render/tasks/{clip_task_id}/file", headers=hb).status_code == 404


# =============== 存储与构造器(纯单元)===============


def test_render_storage_rules():
    rel = storage.save_render_result(424242, _MINI_MP4)
    assert rel == "render/r424242.mp4"
    assert storage.resolve(rel).is_file()
    storage.delete_render_file(rel)
    assert not storage.resolve(rel).exists()
    # 不是 mp4 的内容必须拒(防上游回错误页存成视频)
    import pytest as _pytest

    with _pytest.raises(storage.UploadError):
        storage.save_render_result(1, b"<html>error</html>")


def test_drama_payload_builder():
    from app.db.models import DramaShot
    from app.engines.drama.video import api_render_payload

    shot = DramaShot(episode_id=1, seq=3, camera="环绕", duration_s=99,
                     motion_cn="环绕少女一周", prompt_cn="x")
    payload = api_render_payload(shot, None, quality="480p")
    assert payload["duration_s"] == 15 and payload["resolution"] == "480p竖"
    assert "环绕少女一周" in payload["prompt"] and "环绕" in payload["prompt"]
    # 无运动轨时按运镜栏兜底,不许空
    bare = DramaShot(episode_id=1, seq=1, camera="", duration_s=0, action_desc="她抬起头")
    p2 = api_render_payload(bare, None)
    assert p2["duration_s"] == 4 and "抬起头" in p2["prompt"]


def test_clips_payload_builder():
    from app.db.models import MoodClip
    from app.engines.clips.render_input import chunk_render_payload

    clip_row = MoodClip(user_id=1, negative="噪点", clip={
        "shots": [
            {"seq": 1, "camera": "固定", "action_desc": "她站在天台", "prompt_cn": "P1"},
            {"seq": 2, "camera": "推", "action_desc": "风吹头发", "prompt_cn": "P2"},
        ],
        "chunks": [{"index": 0, "start_s": 2, "end_s": 40, "shot_seqs": [1, 2]}],
    })
    payload = chunk_render_payload(clip_row, clip_row.clip["chunks"][0], quality="768p")
    assert payload["duration_s"] == 15  # 38s 夹到 15
    assert "她站在天台" in payload["prompt"] and "风吹头发" in payload["prompt"]
    assert "噪点" in payload["prompt"] and payload["resolution"] == "768p竖"
