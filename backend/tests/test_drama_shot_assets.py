# tests/test_drama_shot_assets.py
# -*- coding: utf-8 -*-
"""逐格挂素材 + 打勾(那份「逐格施工单」的进度层)。

为什么要有这一层、为什么要单测:
- 一集几十格,出图/生视频都在本站之外一格一格做,做到哪儿全靠人脑记 =
  必然做丢或重做。所以出好的静帧要能挂回原格,两个打勾栏是进度条;
- 挂了静帧那一格就算做完了,自动打勾;删到一张不剩要把勾撤掉,
  否则站里显示「做完了」而实际没有图,进度条就成了骗人的;
- 段计划的首帧图来自段首格的静帧:挂没挂上直接决定这一段能不能开工,
  所以段表要标 first_frame_ready;
- 上传是攻击面:文件名一律服务端生成(定妆照与分镜静帧共用项目目录,
  卡 id 与分镜 id 会撞号,所以静帧带 shot 前缀),路径必须过白名单;
- 别人的分镜一律 404(这一批新加的四条路由全部要盖到)。
"""
from __future__ import annotations

import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.engines.drama.video import clips_payload
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


def _project(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _png() -> bytes:
    """真的 1x1 PNG(不是随手拼的字节):既过文件头校验,也确实是张图。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _seed_episode(pid: int, specs=((4, ""), (4, ""))) -> tuple[int, list[int]]:
    """种一集 + 若干分镜格((秒, 台词) 序列),返回 (集 id, 分镜 id 列表)。"""
    from app.db.models import DramaEpisode, DramaShot, DramaStyleCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        s.add(DramaStyleCard(project_id=pid, style_name="国风厚涂", style_cn="国风厚涂",
                             style_en="ink wash", negative="文字水印", ratio="9:16"))
        ep = DramaEpisode(project_id=pid, ep_index=1, title="雪夜", source_chapters=[1])
        s.add(ep)
        s.flush()
        ids = []
        for i, (dur, dia) in enumerate(specs, start=1):
            shot = DramaShot(
                episode_id=ep.id, seq=i, scene_name="雪道", characters=["沈砚"],
                action_desc="拔刀", shot_type="近景", camera="推", duration_s=dur,
                dialogue=dia, prompt_cn="沈砚,黑短发,雪夜,国风厚涂",
                prompt_en="draw sword", negative="文字水印",
            )
            s.add(shot)
            s.flush()
            ids.append(shot.id)
        s.commit()
        return ep.id, ids


# =============== 挂静帧:挂上即算这一格做完 ===============

def test_upload_asset_attaches_and_ticks_done(client):
    headers = _auth(client, "asset_up")
    pid = _project(client, headers, "挂素材漫剧书")
    _, shot_ids = _seed_episode(pid)
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset"

    r = client.post(base, headers=headers,
                    files={"file": ("a.png", _png(), "image/png")},
                    data={"note": "第二版"})
    assert r.status_code == 200, r.text
    shot = r.json()["shot"]
    assert len(shot["assets"]) == 1
    asset = shot["assets"][0]
    assert asset["kind"] == "upload" and asset["note"] == "第二版"
    # 文件名由服务端生成,且带 shot 前缀(与定妆照同目录,卡 id 会撞号)
    assert asset["src"] == f"drama/{pid}/shot{shot_ids[0]}-1.png"
    assert "a.png" not in asset["src"]
    assert shot["done_still"] is True          # 挂上就算做完,不用再手点一次勾
    assert shot["done_video"] is False         # 视频那一步不受影响
    # 落盘的确实是我们传的那张图
    assert storage.resolve(asset["src"]).read_bytes() == _png()


def test_read_asset_serves_image_and_url_kind_is_404(client):
    headers = _auth(client, "asset_read")
    pid = _project(client, headers, "读素材漫剧书")
    _, shot_ids = _seed_episode(pid, ((4, ""),))
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset"

    client.post(base, headers=headers, files={"file": ("a.png", _png(), "image/png")})
    r = client.get(f"{base}/0", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == _png()
    assert "private" in r.headers.get("cache-control", "")   # 不许中间层缓存私有资产
    assert client.get(f"{base}/9", headers=headers).status_code == 404

    # 外链那张没有文件可读 → 404(不是 500)
    client.post(f"{base}/link", headers=headers,
                json={"url": "https://example.com/a.png"})
    assert client.get(f"{base}/1", headers=headers).status_code == 404


def test_link_asset_requires_http_url(client):
    headers = _auth(client, "asset_link")
    pid = _project(client, headers, "外链素材漫剧书")
    _, shot_ids = _seed_episode(pid, ((4, ""),))
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset/link"

    r = client.post(base, headers=headers, json={"url": "file:///d:/a.png"})
    assert r.status_code == 400 and "http" in r.json()["detail"]

    r = client.post(base, headers=headers,
                    json={"url": "https://cdn.example.com/x.png", "note": "即梦出的"})
    assert r.status_code == 200, r.text
    shot = r.json()["shot"]
    assert shot["assets"] == [
        {"kind": "url", "src": "https://cdn.example.com/x.png", "note": "即梦出的"}
    ]
    assert shot["done_still"] is True


def test_only_real_images_accepted_and_two_per_shot(client):
    headers = _auth(client, "asset_limit")
    pid = _project(client, headers, "上限素材漫剧书")
    _, shot_ids = _seed_episode(pid, ((4, ""),))
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset"

    # 改了扩展名的假图:按文件头判定,一律打回
    r = client.post(base, headers=headers,
                    files={"file": ("fake.png", b"not an image", "image/png")})
    assert r.status_code == 400 and "PNG" in r.json()["detail"]

    for i in range(storage.MAX_ASSETS_PER_SHOT):
        r = client.post(base, headers=headers,
                        files={"file": (f"{i}.png", _png(), "image/png")})
        assert r.status_code == 200, r.text
    r = client.post(base, headers=headers,
                    files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400 and "最多挂" in r.json()["detail"]


def test_delete_last_asset_unticks_done_and_removes_file(client):
    headers = _auth(client, "asset_del")
    pid = _project(client, headers, "删素材漫剧书")
    _, shot_ids = _seed_episode(pid, ((4, ""),))
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset"

    src = client.post(base, headers=headers,
                      files={"file": ("a.png", _png(), "image/png")}
                      ).json()["shot"]["assets"][0]["src"]
    client.post(f"{base}/link", headers=headers, json={"url": "https://e.com/b.png"})

    # 删掉上传那张:连文件一起删,但还剩外链那张 → 勾不动
    r = client.delete(f"{base}/0", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["shot"]["assets"]) == 1
    assert r.json()["shot"]["done_still"] is True
    assert not storage.resolve(src).exists()

    # 删到一张不剩:勾要撤掉——留着勾等于站里显示「做完了」而其实没有图
    r = client.delete(f"{base}/0", headers=headers)
    assert r.json()["shot"]["assets"] == []
    assert r.json()["shot"]["done_still"] is False
    assert client.delete(f"{base}/0", headers=headers).status_code == 404


def test_patch_shot_takes_ticks_and_clip_ref(client):
    headers = _auth(client, "asset_patch")
    pid = _project(client, headers, "打勾漫剧书")
    ep_id, shot_ids = _seed_episode(pid, ((4, ""),))
    base = f"/api/projects/{pid}/drama/shots/{shot_ids[0]}"

    r = client.patch(base, headers=headers,
                     json={"done_video": True, "clip_ref": "D:/漫剧/雪夜-段1.mp4"})
    assert r.status_code == 200, r.text
    shot = r.json()["shot"]
    assert shot["done_video"] is True and shot["clip_ref"] == "D:/漫剧/雪夜-段1.mp4"
    assert shot["done_still"] is False        # 没让它动的栏不许被顺手改掉
    assert shot["prompt_cn"].startswith("沈砚")

    # 取消打勾也是正常操作(False 不能被当成「没传」丢掉)
    assert client.patch(base, headers=headers,
                        json={"done_video": False}).json()["shot"]["done_video"] is False

    # 集详情带集级进度:一集几十格,做到哪儿要一眼看见
    prog = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}",
                      headers=headers).json()["progress"]
    assert prog == {"shots": 1, "stills_done": 0, "videos_done": 0, "assets": 0}


def test_episode_progress_counts_ticks(client):
    headers = _auth(client, "asset_prog")
    pid = _project(client, headers, "进度漫剧书")
    ep_id, shot_ids = _seed_episode(pid, ((4, ""), (4, ""), (4, "")))
    client.post(f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset", headers=headers,
                files={"file": ("a.png", _png(), "image/png")})
    client.patch(f"/api/projects/{pid}/drama/shots/{shot_ids[1]}", headers=headers,
                 json={"done_video": True})
    prog = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}",
                      headers=headers).json()["progress"]
    assert prog == {"shots": 3, "stills_done": 1, "videos_done": 1, "assets": 1}


# =============== 段计划:首帧图挂没挂上,直接决定这一段能不能开工 ===============

def test_clip_plan_marks_first_frame_ready(client):
    headers = _auth(client, "asset_clip")
    pid = _project(client, headers, "首帧就位漫剧书")
    ep_id, shot_ids = _seed_episode(pid, ((4, ""), (4, ""), (4, "")))
    base = f"/api/projects/{pid}/drama/episodes/{ep_id}/clips?limit_s=10"

    plan = client.get(base, headers=headers).json()["plan"]
    assert [s["seqs"] for s in plan["segments"]] == [[1, 2], [3]]
    assert [s["first_frame_ready"] for s in plan["segments"]] == [False, False]
    assert plan["totals"]["first_frames_ready"] == 0
    assert "首帧图已就位 0/2 段" in plan["note"]

    # 只给第 1 格挂静帧:第一段(首帧取段首格)亮,第二段还没有
    client.post(f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset", headers=headers,
                files={"file": ("a.png", _png(), "image/png")})
    plan = client.get(base, headers=headers).json()["plan"]
    assert [s["first_frame_ready"] for s in plan["segments"]] == [True, False]
    assert plan["totals"]["first_frames_ready"] == 1
    assert "首帧图已就位 1/2 段" in plan["note"]

    # 挂在第 2 格没用:并段后首帧一律取段首格,标记不能因此亮
    client.post(f"/api/projects/{pid}/drama/shots/{shot_ids[1]}/asset", headers=headers,
                files={"file": ("b.png", _png(), "image/png")})
    plan = client.get(base, headers=headers).json()["plan"]
    assert [s["first_frame_ready"] for s in plan["segments"]] == [True, False]


def test_clips_payload_tolerates_shots_without_the_new_columns():
    """老库/替身对象没有 assets、done_still 两个属性时按「没挂」处理,不能炸。"""
    class _Shot:
        def __init__(self, seq):
            self.seq, self.scene_name, self.characters = seq, "雪道", ["沈砚"]
            self.action_desc, self.shot_type, self.camera = "拔刀", "近景", "推"
            self.dialogue, self.duration_s = "", 4
            self.prompt_cn, self.prompt_en, self.negative = "沈砚", "en", ""
            self.motion_cn = self.motion_en = ""

    plan = clips_payload([_Shot(1)], None, 10)
    assert plan["segments"][0]["first_frame_ready"] is False
    assert plan["totals"]["first_frames_ready"] == 0


# =============== 导出:施工单带上真实进度 ===============

def test_export_shows_progress_and_ticks(client):
    headers = _auth(client, "asset_export")
    pid = _project(client, headers, "导出进度漫剧书")
    # 8+8=16 秒超过默认上限 10 → 拆成两段,才能同时看到「首帧已挂」与「待出图」两种标记
    ep_id, shot_ids = _seed_episode(pid, ((8, ""), (8, "")))
    client.post(f"/api/projects/{pid}/drama/shots/{shot_ids[0]}/asset", headers=headers,
                files={"file": ("a.png", _png(), "image/png")})
    client.patch(f"/api/projects/{pid}/drama/shots/{shot_ids[0]}", headers=headers,
                 json={"done_video": True, "clip_ref": "seg1.mp4"})
    base = f"/api/projects/{pid}/drama/episodes/{ep_id}/export"

    md = client.get(f"{base}?format=md", headers=headers).text
    assert "施工进度:静帧 1/2 格 | 视频 1/2 格 | 已挂素材 1 张" in md
    assert "✓ 已挂" in md and "待出图" in md      # 段表标首帧图就位没

    csv_text = client.get(f"{base}?format=csv", headers=headers).text
    header = csv_text.splitlines()[0]
    for col in ("still_asset", "clip_ref", "done_still", "done_video"):
        assert col in header, col
    assert f"drama/{pid}/shot{shot_ids[0]}-1.png" in csv_text   # 挂的图写进施工单
    assert "seg1.mp4" in csv_text
    assert "✓" in csv_text                                      # 站内勾过的导出也是勾上的


# =============== 归属:别人的分镜一格也碰不到 ===============

def test_shot_assets_isolate_other_users(client):
    headers = _auth(client, "asset_owner")
    pid = _project(client, headers, "别人的挂素材书")
    _, shot_ids = _seed_episode(pid, ((4, ""),))
    sid = shot_ids[0]
    base = f"/api/projects/{pid}/drama/shots/{sid}/asset"
    client.post(base, headers=headers, files={"file": ("a.png", _png(), "image/png")})
    thief = _auth(client, "asset_thief")

    assert client.post(base, headers=thief,
                       files={"file": ("a.png", _png(), "image/png")}).status_code == 404
    assert client.post(f"{base}/link", headers=thief,
                       json={"url": "https://e.com/a.png"}).status_code == 404
    assert client.get(f"{base}/0", headers=thief).status_code == 404
    assert client.delete(f"{base}/0", headers=thief).status_code == 404
    # 主人的图一张没少
    assert len(client.get(f"/api/projects/{pid}/drama/shots/{sid}/asset/0",
                          headers=headers).content) > 0


# =============== 落盘层:两种资产共用一个目录,不许撞号 ===============

def test_shot_asset_filenames_never_collide_with_character_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "upload_root", lambda: tmp_path)
    card_rel = storage.save_character_ref(7, 7, _png(), 0)
    shot_rel = storage.save_shot_asset(7, 7, _png(), 0)
    assert card_rel == "drama/7/7-1.png"
    assert shot_rel == "drama/7/shot7-1.png"       # 卡 7 与第 7 格不能是同一个文件
    assert card_rel != shot_rel
    # 两条路径都要过白名单(shot 前缀是新加的,别把自己的文件挡在外面)
    assert storage.resolve(card_rel).is_file()
    assert storage.resolve(shot_rel).is_file()
    with pytest.raises(storage.UploadError):
        storage.resolve("drama/7/../../etc/passwd")
