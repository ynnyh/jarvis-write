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
from app.engines.render.client import RenderError
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


# =============== 对白链:情绪映射 / wav 解析 / 音色上传 ===============


def test_emotion_weights():
    from app.engines.render.emotion import emotion_weights, normalize_emotion

    # 空与脏值 → 平静兜底
    calm = emotion_weights("")
    assert calm["emo_calm"] == 0.6 and calm["emo_random"] is False
    assert emotion_weights("不存在的情绪")["emo_calm"] == 0.6
    # 主情绪 0.8,平静垫底
    happy = emotion_weights("happy")
    assert happy["emo_happy"] == 0.8 and happy["emo_calm"] == 0.2
    assert happy["emo_control_method"]
    assert normalize_emotion("HAPPY") == "happy"


def test_wav_duration_parse():
    import struct

    from app.engines.render.service import wav_duration_s

    def wav(rate: int, seconds: float) -> bytes:
        # 单声道 8bit:byte_rate = 采样率 × 1 × 8/8 = 采样率,时长 = data 大小 / 采样率
        data = b"\x00" * int(rate * seconds)
        fmt = struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
        return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
                + b"fmt " + struct.pack("<I", len(fmt)) + fmt
                + b"data" + struct.pack("<I", len(data)) + data)

    assert wav_duration_s(wav(8000, 3)) == 3.0
    assert abs(wav_duration_s(wav(44100, 2.5)) - 2.5) < 0.01
    assert wav_duration_s(b"<html>not wav</html>") == 0.0
    assert wav_duration_s(b"") == 0.0


def test_audio_sniff_and_voice_upload(client: TestClient):
    import pytest as _pytest

    headers = _auth(client, "render_voice_a")
    pid = client.post("/api/projects", headers=headers, json={"title": "音色书"}).json()["id"]
    from app.db.models import DramaCharacterCard
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        card = DramaCharacterCard(project_id=pid, name="林小满")
        db.add(card)
        db.flush()
        cid = card.id
        db.commit()

    # 非音频内容必须拒
    r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/voice",
                    headers=headers, files={"file": ("a.mp3", b"<html>no</html>", "audio/mpeg")})
    assert r.status_code == 400
    # ID3 头的 mp3 → 上传成功,卡上带 voice_ref
    mp3 = b"ID3\x04\x00" + b"\x00" * 256
    r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/voice",
                    headers=headers, files={"file": ("a.mp3", mp3, "audio/mpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["card"]["voice_ref"].startswith("drama/")
    rel = r.json()["card"]["voice_ref"]
    # 试听走鉴权端点;再传 wav 覆盖(固定名重传即换)
    r = client.get(f"/api/projects/{pid}/drama/characters/{cid}/voice", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"fmt " + b"\x00" * 20
    r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/voice",
                    headers=headers, files={"file": ("b.wav", wav, "audio/wav")})
    assert r.json()["card"]["voice_ref"].endswith(".wav")
    # 删除:文件与字段一起清
    r = client.delete(f"/api/projects/{pid}/drama/characters/{cid}/voice", headers=headers)
    assert r.status_code == 200 and r.json()["card"]["voice_ref"] == ""
    assert not storage.resolve(rel).exists()  # 文件连字段一起清了
    # B 看不到 A 的音色端点(归属隔离)
    hb = _auth(client, "render_voice_b")
    assert client.get(f"/api/projects/{pid}/drama/characters/{cid}/voice", headers=hb).status_code == 404


# =============== 对白链:路由分支与全流程 ===============


def _seed_talk_scene(client: TestClient, username: str, *, with_voice: bool, with_still: bool = True):
    """种一套对白出片的料:书(完整档)+ 集带剧本 lines + 有台词的分镜格(+角色音色)。

    返回 (headers, pid, shot_id, card_id)。
    """
    from app.db.models import DramaCharacterCard, DramaEpisode, DramaShot
    from app.db.session import SessionLocal

    headers = _auth(client, username)
    pid = client.post("/api/projects", headers=headers, json={"title": "对白书"}).json()["id"]
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"render_mode": "full"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        card = DramaCharacterCard(project_id=pid, name="林小满")
        db.add(card)
        db.flush()
        cid = card.id
        db.commit()
        ep = DramaEpisode(
            project_id=pid, ep_index=1, title="第一集",
            script={"lines": [{"speaker": "林小满", "text": "你到底想说什么"}]},
        )
        db.add(ep)
        db.flush()
        shot = DramaShot(
            episode_id=ep.id, seq=1, camera="推", duration_s=3,
            dialogue="你到底想说什么", emotion="happy",
        )
        db.add(shot)
        db.flush()
        if with_still:
            # 挂静帧记录 + 真实文件(resolve 白名单校验与 is_file 都要过)
            from app import storage as _storage

            rel = f"drama/{pid}/shot{shot.id}-1.png"
            d = _storage.upload_root() / "drama" / str(pid)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"shot{shot.id}-1.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + bytes(32))
            shot.assets = [{"kind": "upload", "src": rel, "note": ""}]
        db.commit()
    if with_voice:
        mp3 = b"ID3\x04\x00" + b"\x00" * 128
        r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/voice",
                        headers=headers, files={"file": ("v.mp3", mp3, "audio/mpeg")})
        assert r.status_code == 200, r.text
    _config_token(client, headers)
    shot_id = _shot_id_of(client, headers, pid)
    return headers, pid, shot_id, cid


def _shot_id_of(client: TestClient, headers: dict, pid: int) -> int:
    eps = client.get(f"/api/projects/{pid}/drama/episodes", headers=headers).json()["episodes"]
    detail = client.get(f"/api/projects/{pid}/drama/episodes/{eps[0]['id']}", headers=headers).json()
    return detail["shots"][0]["id"]


def test_talk_routing_branches(client: TestClient):
    """三分支:全料→talk;缺音色→i2v+note;缺静帧→t2v+note;轻量档→普通出片。"""
    # ① 全料(音色+静帧+完整档)→ talk
    headers, pid, sid, _ = _seed_talk_scene(client, "talk_full", with_voice=True)
    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r.status_code == 200, r.text
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert fake.specs[0]["kind"] == "talk"
    assert fake.specs[0]["talk"]["text"] == "你到底想说什么"
    assert fake.specs[0]["talk"]["emotion"] == "happy"
    # talk 走对口型工作流(配置里没填时回落默认)
    assert fake.specs[0]["workflow_id"] == "minimax_h3_image_audio_to_video"

    # ② 缺音色 → 回退 i2v,params.note 说明原因
    headers2, pid2, sid2, _ = _seed_talk_scene(client, "talk_novoice", with_voice=False)
    fake2 = _FakeRender()
    with patch("app.api.render.start_render", fake2):
        r = client.post(f"/api/projects/{pid2}/drama/shots/{sid2}/render", headers=headers2)
        assert _wait_job(client, headers2, r.json()["job_id"])["status"] == "done"
    assert fake2.specs[0]["kind"] == "i2v"
    assert "音色" in fake2.specs[0]["params"].get("note", "")

    # ③ 缺静帧 → 对白链开不了工,回退 t2v + note
    headers3, pid3, sid3, _ = _seed_talk_scene(client, "talk_nostill", with_voice=True, with_still=False)
    fake3 = _FakeRender()
    with patch("app.api.render.start_render", fake3):
        r = client.post(f"/api/projects/{pid3}/drama/shots/{sid3}/render", headers=headers3)
        assert _wait_job(client, headers3, r.json()["job_id"])["status"] == "done"
    assert fake3.specs[0]["kind"] == "t2v"
    assert "静帧" in fake3.specs[0]["params"].get("note", "")

    # ④ 轻量档:有台词有音色也走普通 i2v,无 note
    headers4, pid4, sid4, _ = _seed_talk_scene(client, "talk_lite", with_voice=True)
    client.patch(f"/api/projects/{pid4}", headers=headers4, json={"render_mode": "lite"})
    fake4 = _FakeRender()
    with patch("app.api.render.start_render", fake4):
        r = client.post(f"/api/projects/{pid4}/drama/shots/{sid4}/render", headers=headers4)
        assert _wait_job(client, headers4, r.json()["job_id"])["status"] == "done"
    assert fake4.specs[0]["kind"] == "i2v"
    assert "note" not in fake4.specs[0]["params"]


def test_talk_full_pipeline_with_tts_cache(client: TestClient):
    """真 service 链路(打桩平台 client):TTS 未命中→合成入库;重 roll 命中缓存不再付费。

    submit 按工作流分账:indextts2 一次 + 对口型一次;第二轮只有对口型一次。
    """
    from app.db.models import RenderTask, TtsTrack
    from app.db.session import SessionLocal

    headers, pid, sid, _ = _seed_talk_scene(client, "talk_pipeline", with_voice=True)
    _wav_bytes = _make_wav(rate=8000, seconds=3)

    calls: list[str] = []
    state = {"kind": "wav"}  # fetch_bytes 按它返回对应假文件

    async def fake_submit(base_url, token, workflow_id, params):
        calls.append(workflow_id)
        state["kind"] = "wav" if "indextts2" in workflow_id else "mp4"
        # TTS 参数断言:台词/音色/情感权重都在;对口型参数断言:首帧/音频/时长
        if "indextts2" in workflow_id:
            assert params["prompt_text"] == "你到底想说什么"
            assert params["prompt_simple"].startswith("data:audio/")
            assert params["emo_happy"] == 0.8
        else:
            assert params["ref_image_0"].startswith("data:image/")
            assert params["ref_audio_0"].startswith("data:audio/")
        return f"pt-{len(calls)}"

    async def fake_poll(base_url, token, task_id):
        return "success", ["https://fake/result"]

    async def fake_fetch(url, timeout_s=120):
        return _make_wav(8000, 3) if state["kind"] == "wav" else _MINI_MP4

    with patch("app.engines.render.service.submit", fake_submit), \
         patch("app.engines.render.service.poll_with_retry", fake_poll), \
         patch("app.engines.render.service.fetch_bytes", fake_fetch):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done", r.text
        task_id_1 = r.json()["task_id"]
        # 重 roll:TTS 走缓存,只调对口型一次
        r2 = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        job2 = _wait_job(client, headers, r2.json()["job_id"])
        assert job2["status"] == "done", job2

    # 调用账本:第一轮 TTS+对口型 2 次,第二轮缓存命中只对口型 1 次
    assert calls == ["indextts2-v1", "minimax_h3_image_audio_to_video",
                     "minimax_h3_image_audio_to_video"]
    with SessionLocal() as db:
        t1 = db.get(RenderTask, task_id_1)
        assert t1.kind == "talk" and t1.status == "success"
        assert t1.params["duration_s"] == 3 and t1.params["tts_cached"] is False
        rows = db.query(TtsTrack).all()
        assert len(rows) == 1 and rows[0].duration_s == 3.0
        # 两版草片都回写成功,指针指向最新一版
        from app.db.models import DramaShot

        assert db.get(DramaShot, sid).clip_ref


def test_result_url_ssrf_blocked(client: TestClient):
    """平台返回的成片地址指向内网:拒绝下载,任务失败并说人话,不当内网跳板。"""
    headers, pid, sid, _ = _seed_talk_scene(client, "ssrf_result", with_voice=False)

    async def fake_submit(base_url, token, workflow_id, params):
        return "pt-ssrf"

    async def fake_poll(base_url, token, task_id):
        return "success", ["http://10.0.0.5/v.mp4"]

    async def must_not_fetch(url, timeout_s=120):
        raise AssertionError("should have been blocked by net_guard")

    with patch("app.engines.render.service.submit", fake_submit), \
         patch("app.engines.render.service.poll_with_retry", fake_poll), \
         patch("app.engines.render.service.fetch_bytes", must_not_fetch):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error", job
    assert "内网" in job["error"]


def test_presubmit_failure_marks_task_failed(client: TestClient):
    """submit 前失败(此处:提交被平台拒)也要把任务行标成 failed,不许永挂「排队中」。"""
    headers, pid, sid, _ = _seed_talk_scene(client, "presubmit_fail", with_voice=False)

    async def boom(base_url, token, workflow_id, params):
        raise RenderError("提交就被平台拒了(测试)")

    with patch("app.engines.render.service.submit", boom):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "error", job
    tasks = client.get(
        f"/api/projects/{pid}/drama/shots/{sid}/render/tasks", headers=headers
    ).json()["tasks"]
    assert tasks[0]["status"] == "failed", tasks[0]
    assert "平台拒了" in tasks[0]["error"]


def _make_wav(rate: int, seconds: float) -> bytes:
    import struct

    data = b"\x00" * int(rate * seconds)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


def test_talk_long_audio_truncated_note(client: TestClient):
    """配音超过 15s:不失败,照常出片,但 task.params.note 如实标注已截断。"""
    from app.db.session import SessionLocal

    headers, pid, sid, _ = _seed_talk_scene(client, "talk_long", with_voice=True)

    async def fake_submit(base_url, token, workflow_id, params):
        return f"pt-{workflow_id}"

    async def fake_poll(base_url, token, task_id):
        return "success", ["https://fake/result"]

    state = {"kind": "wav"}

    async def fake_fetch(url, timeout_s=120):
        w = _make_wav(8000, 20) if state["kind"] == "wav" else _MINI_MP4
        state["kind"] = "mp4"
        return w

    with patch("app.engines.render.service.submit", fake_submit), \
         patch("app.engines.render.service.poll_with_retry", fake_poll), \
         patch("app.engines.render.service.fetch_bytes", fake_fetch):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid}/render", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
        assert job["status"] == "done", job

    with SessionLocal() as db:
        from app.db.models import RenderTask as RT

        row = db.query(RT).filter(RT.shot_id == sid).first()
        assert row.params["duration_s"] == 15  # 20s 夹到上限
        assert "截断" in row.params.get("note", "")


# =============== 末帧自动接力 ===============


def test_ffmpeg_bin_resolution(monkeypatch, tmp_path):
    """三级定位:环境变量 > 打包资源 bin/ > PATH;全空返回 None。"""
    from app.engines.render import ffmpeg as ff

    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"x")
    monkeypatch.setenv("JARVIS_FFMPEG", str(fake))
    assert ff.ffmpeg_bin() == str(fake)
    # 环境变量指向不存在的文件 → 落到 PATH 探测(测试机多半没有,允许 None)
    monkeypatch.setenv("JARVIS_FFMPEG", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    assert ff.ffmpeg_bin() is None
    assert ff.available() is False


def test_extract_last_frame_command(monkeypatch, tmp_path):
    """抽帧命令拼装:-sseof 倒搜、单帧、高质量;命令成功且产物存在才算成功。"""
    from app.engines.render import ffmpeg as ff

    mp4 = tmp_path / "r1.mp4"
    mp4.write_bytes(_MINI_MP4)
    out = tmp_path / "lf" / "r1.png"
    captured = {}

    def fake_run(cmd, capture_output, timeout):
        captured["cmd"] = cmd
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG\r\n\x1a\n")
        class _P:
            returncode = 0
            stderr = b""
        return _P()

    monkeypatch.setattr(ff, "ffmpeg_bin", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    assert ff.extract_last_frame(mp4, out) is True
    cmd = captured["cmd"]
    assert "-sseof" in cmd and "0.1" in cmd
    assert "-frames:v" in cmd and str(out) in cmd
    # 命令失败 → False 不抛
    monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 1, "stderr": b"x"})())
    assert ff.extract_last_frame(mp4, out) is False


def test_prev_frames_relay(client: TestClient):
    """接力全流程:上一格出片存末帧 → 整集清单亮出候选 → 一键挂为本格静帧。"""
    from app.db.session import SessionLocal

    headers = _auth(client, "relay_a")
    pid = client.post("/api/projects", headers=headers, json={"title": "接力书"}).json()["id"]
    from app.db.models import DramaEpisode, DramaShot
    from app.db.session import SessionLocal as _SL

    with _SL() as db:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="第一集")
        db.add(ep)
        db.flush()
        s1 = DramaShot(episode_id=ep.id, seq=1, camera="固定", duration_s=3)
        s2 = DramaShot(episode_id=ep.id, seq=2, camera="推", duration_s=3)
        db.add_all([s1, s2])
        db.commit()
        sid1, sid2 = s1.id, s2.id
    _config_token(client, headers)

    # 第 1 格出片(fake 引擎),然后手工补上末帧文件(模拟 ffmpeg 抽帧的产物)
    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid1}/render", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    task_id = r.json()["task_id"]
    lf_dir = storage.upload_root() / "render" / "lf"
    lf_dir.mkdir(parents=True, exist_ok=True)
    (lf_dir / f"r{task_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    # 整集清单:第 2 格亮出「上一格末帧」候选;第 1 格没有
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep.id}/prev-frames", headers=headers)
    by_seq = r.json()["by_seq"]
    assert by_seq.get("2", {}).get("task_id") == task_id
    assert by_seq.get("2", {}).get("from_seq") == 1
    assert "1" not in by_seq

    # 末帧文件鉴权可读
    r = client.get(f"/api/render/tasks/{task_id}/last-frame", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"

    # 一键采纳:第 2 格挂上末帧静帧 + 自动勾「静帧出好了」
    r = client.post(f"/api/projects/{pid}/drama/shots/{sid2}/adopt-prev-frame", headers=headers)
    assert r.status_code == 200, r.text
    shot2 = r.json()["shot"]
    assert shot2["done_still"] is True
    assert "末帧" in shot2["assets"][0]["note"]

    # 挂满 2 张再采纳 → 400;第 1 格(没有上一格)→ 404;他人项目 → 404
    from app.db.models import DramaShot as DS

    with _SL() as db:
        db.get(DS, sid2).assets = shot2["assets"] + [{"kind": "upload", "src": "drama/1/shot9-1.png", "note": ""}]
        db.commit()
    r = client.post(f"/api/projects/{pid}/drama/shots/{sid2}/adopt-prev-frame", headers=headers)
    assert r.status_code == 400
    r = client.post(f"/api/projects/{pid}/drama/shots/{sid1}/adopt-prev-frame", headers=headers)
    assert r.status_code == 404
    hb = _auth(client, "relay_b")
    assert client.post(f"/api/projects/{pid}/drama/shots/{sid2}/adopt-prev-frame", headers=hb).status_code == 404


def test_purge_removes_last_frame(client: TestClient):
    """删项目时,末帧文件跟着渲染任务一起清(不留在卷里吃空间)。"""
    headers = _auth(client, "relay_purge")
    pid = client.post("/api/projects", headers=headers, json={"title": "清库书"}).json()["id"]
    from app.db.models import DramaEpisode, DramaShot, RenderTask
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="e1")
        db.add(ep)
        db.flush()
        s = DramaShot(episode_id=ep.id, seq=1, duration_s=3)
        db.add(s)
        db.flush()
        t = RenderTask(user_id=1, line="drama", project_id=pid, shot_id=s.id,
                       kind="i2v", status="success", result_path="render/r999.mp4")
        db.add(t)
        db.flush()
        lf = storage.upload_root() / "render" / "lf" / f"r{t.id}.png"
        lf.parent.mkdir(parents=True, exist_ok=True)
        lf.write_bytes(b"\x89PNG\r\n\x1a\n")
        lf_id = t.id
        db.commit()
    assert client.delete(f"/api/projects/{pid}", headers=headers).status_code == 200
    assert not (storage.upload_root() / "render" / "lf" / f"r{lf_id}.png").exists()


# =============== 整集一键合成 ===============


def _make_plan_db(client: TestClient, username: str, *, full: bool = True):
    """种一集三格:①有本站草片+配音(talk) ②只有静帧 ③什么都没有。"""
    from app.db.models import DramaEpisode, DramaShot
    from app.db.session import SessionLocal

    headers = _auth(client, username)
    pid = client.post("/api/projects", headers=headers, json={"title": "合成书"}).json()["id"]
    if full:
        client.patch(f"/api/projects/{pid}", headers=headers, json={"render_mode": "full"})
    _config_token(client, headers)  # 出片前置:引擎令牌
    with SessionLocal() as db:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="第一集")
        db.add(ep)
        db.flush()
        s1 = DramaShot(episode_id=ep.id, seq=1, camera="固定", duration_s=3, dialogue="第一句")
        s2 = DramaShot(episode_id=ep.id, seq=2, camera="推", duration_s=4, dialogue="第二句")
        s3 = DramaShot(episode_id=ep.id, seq=3, camera="拉", duration_s=3)
        db.add_all([s1, s2, s3])
        db.commit()
        return headers, pid, ep.id, s1.id, s2.id, s3.id


def test_collect_plan_branches(client: TestClient):
    from app.engines.render.synthesis import collect_plan

    headers, pid, eid, sid1, sid2, sid3 = _make_plan_db(client, "synth_collect")
    # 第 1 格出片(引擎回写 clip_ref)+ 最新任务标记为 talk(有配音音轨)
    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid1}/render", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    from app.db.models import DramaShot, RenderTask
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        t = db.query(RenderTask).filter(RenderTask.shot_id == sid1).first()
        t.kind = "talk"  # 假引擎不知道情感链,这里手工标记「有配音」
        db.commit()
        shot1 = db.get(DramaShot, sid1)
        # 给第 2 格挂一张静帧
        d = storage.upload_root() / "drama" / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"shot{sid2}-1.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + bytes(16))
        shot2 = db.get(DramaShot, sid2)
        shot2.assets = [{"kind": "upload", "src": f"drama/{pid}/shot{sid2}-1.png", "note": ""}]
        db.commit()

        shots = db.query(DramaShot).filter(DramaShot.episode_id == eid).order_by(DramaShot.seq).all()
        with patch("app.engines.render.synthesis.probe_clip",
                   lambda p: {"duration_s": 3.0, "has_audio": False, "width": 720, "height": 1280}):
            plan = collect_plan(db, shots)
    assert plan.clip_count == 1 and plan.still_count == 1
    assert plan.skipped_seqs == [3]
    assert plan.items[0].has_audio is True and plan.items[0].text == "第一句"
    assert plan.items[1].kind == "still" and plan.items[1].has_audio is False
    assert (plan.width, plan.height) == (720, 1280)


def test_build_srt_and_command():
    from pathlib import Path

    from app.engines.render.synthesis import SynthItem, SynthPlan, build_command, build_srt

    plan = SynthPlan(items=[
        SynthItem("clip", Path("a.mp4"), 3.0, "第一句", True),
        SynthItem("still", Path("b.png"), 4.0, "第二句", False),
        SynthItem("clip", Path("c.mp4"), 2.5, "", False),
    ], skipped_seqs=[9], width=720, height=1280)
    srt = build_srt(plan)
    assert "00:00:00,000 --> 00:00:03,000" in srt and "第一句" in srt
    assert "00:00:03,000 --> 00:00:07,000" in srt and "第二句" in srt
    # 第三格无台词:时长计入但不出现字幕条
    assert srt.count("-->") == 2

    cmd = build_command(plan, burn_subtitles=True, bgm_path=Path("bg.mp3"),
                        out_path=Path("out/e1-t1.mp4"))
    joined = " ".join(cmd)
    assert "concat=n=3:v=1:a=1" in joined
    assert "anullsrc" in joined and "subtitles=sub.srt" in joined
    assert "volume=0.12" in joined and "-stream_loop" in joined
    # still 输入要 -loop 定时长;clip 普通 -i
    assert cmd.count("-loop") == 1


def test_synth_api_flow(client: TestClient):
    """API 链路:lite 拒 / 无 ffmpeg 拒 / 无片段拒 / 正常合成(fake ffmpeg)+ 旧片清理 + adopt 400。

    ffmpeg 门槛全程打桩:本机装没装、CI runner 有没有都不影响结果,
    「有/没有」两条分支各自显式断言,谁的环境都不许漂。
    """
    headers, pid, eid, sid1, sid2, sid3 = _make_plan_db(client, "synth_api")

    # 轻量档 → 400
    client.patch(f"/api/projects/{pid}", headers=headers, json={"render_mode": "lite"})
    r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/synth",
                    headers=headers, json={"burn_subtitles": True})
    assert r.status_code == 400 and "完整档" in r.json()["detail"]
    client.patch(f"/api/projects/{pid}", headers=headers, json={"render_mode": "full"})

    # 没有 ffmpeg → 400(与档位是两道独立的闸)
    with patch("app.engines.render.ffmpeg.available", return_value=False):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/synth",
                        headers=headers, json={"burn_subtitles": True})
        assert r.status_code == 400 and "ffmpeg" in r.json()["detail"]

    # ffmpeg 有了,但三格都没有草片/静帧 → 400
    with patch("app.engines.render.ffmpeg.available", return_value=True):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/synth",
                        headers=headers, json={"burn_subtitles": True})
        assert r.status_code == 400

    # 给第 1 格出片(fake 引擎)+ 落一个旧成片文件(验证清理)
    fake = _FakeRender()
    with patch("app.api.render.start_render", fake):
        r = client.post(f"/api/projects/{pid}/drama/shots/{sid1}/render", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    old = storage.upload_root() / "render" / "synth" / f"e{eid}-t0.mp4"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(_MINI_MP4)

    def fake_run_synthesis(progress, plan, *, burn_subtitles, bgm_path, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_MINI_MP4)
        assert burn_subtitles is True
        return out_path

    with patch("app.engines.render.synthesis.run_synthesis", fake_run_synthesis), \
         patch("app.engines.render.ffmpeg.available", return_value=True):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/synth",
                        headers=headers, json={"burn_subtitles": True})
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])
        assert job["status"] == "done", job
        task_id = r.json()["task_id"]

    # 状态端点 + 旧成片已清 + 文件端点可读
    synth = client.get(f"/api/projects/{pid}/drama/episodes/{eid}/synth", headers=headers).json()["synth"]
    assert synth and synth["status"] == "success"
    assert not old.exists()
    r = client.get(f"/api/render/tasks/{task_id}/file", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    # 整集成片不参与「设为成片」
    r = client.post(f"/api/render/tasks/{task_id}/adopt", headers=headers)
    assert r.status_code == 400 and "不需要设为成片" in r.json()["detail"]


def test_bgm_roundtrip(client: TestClient):
    from app.db.models import DramaEpisode
    from app.db.session import SessionLocal

    headers = _auth(client, "bgm_a")
    pid = client.post("/api/projects", headers=headers, json={"title": "BGM 书"}).json()["id"]
    with SessionLocal() as db:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="第一集")
        db.add(ep)
        db.commit()
        eid = ep.id

    r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/bgm",
                    headers=headers, files={"file": ("bg.mp3", b"<html>no</html>", "audio/mpeg")})
    assert r.status_code == 400
    mp3 = b"ID3\x04\x00" + b"\x00" * 512
    r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/bgm",
                    headers=headers, files={"file": ("bg.mp3", mp3, "audio/mpeg")})
    assert r.status_code == 200 and r.json()["bgm"].endswith(".mp3")
    r = client.get(f"/api/projects/{pid}/drama/episodes/{eid}/bgm", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    r = client.delete(f"/api/projects/{pid}/drama/episodes/{eid}/bgm", headers=headers)
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/drama/episodes/{eid}/bgm", headers=headers)
    assert r.status_code == 404
