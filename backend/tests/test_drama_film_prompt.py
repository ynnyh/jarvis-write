# tests/test_drama_film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(端到端音频原生视频模型)测试:生成 / 读取 / 保存 / 归属。

直接种分镜与角色卡数据(不走 LLM 前置管线),生成一步 patch 掉 adapter——
验证点:
- 无分镜时生成被拦,job 报错说人话
- 生成跑通:覆盖保存,注入原料(外貌/服饰/台词与说话人)齐活,围栏被剥掉
- GET / PUT:空稿、整段替换保存;手改与粘贴自己的版本走同一列
- 归属隔离:对他人项目的集操作 → 404
"""
from __future__ import annotations

import time
from unittest.mock import patch

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


def _create_project(client: TestClient, headers: dict, title: str = "谍战漫剧书") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


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


class _TextAdapter:
    """假适配器:ask() 返回整段纯文本(整片提示词是纯文本产物,不走 JSON 解析)。"""

    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return self.text


def _seed_episode(pid: int, *, with_shots: bool = True) -> int:
    """种一集:画风卡 + 角色卡 + 剧本 lines + 两格分镜(带台词,供说话人反查)。"""
    from app.db.models import DramaCharacterCard, DramaEpisode, DramaShot, DramaStyleCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        s.add(DramaStyleCard(
            project_id=pid, style_name="写实谍战", ratio="9:16",
            style_cn="写实电影质感,轻微胶片颗粒,暗金与墨绿的沉郁色系,侧光为主",
            negative="文字水印、五官错位、现代物品",
        ))
        s.add(DramaCharacterCard(
            project_id=pid, name="苏曼", gender="female",
            appearance_cn="黑色卷发贴近脸侧,眼睛明亮带警惕,五官精致",
            outfit_cn="深宝蓝丝质旗袍,剪裁贴合",
            voice_desc="柔软清亮,带一点紧张后的沙哑",
        ))
        ep = DramaEpisode(
            project_id=pid, ep_index=1, title="试探", status="storyboarded",
            script={"lines": [{"speaker": "苏曼", "text": "你为什么还要听?"}]},
        )
        s.add(ep)
        s.flush()
        if with_shots:
            s.add(DramaShot(
                episode_id=ep.id, seq=1, scene_name="茶室", characters=["苏曼"],
                shot_type="近景", camera="固定", duration_s=4,
                action_desc="她垂眼看戒指,指尖停在杯沿,再抬眼",
                dialogue="你为什么还要听?",
            ))
            s.add(DramaShot(
                episode_id=ep.id, seq=2, scene_name="茶室", characters=["苏曼"],
                shot_type="特写", camera="推", duration_s=3,
                action_desc="她眼眶泛起微弱水光,把话咽了回去", dialogue="",
            ))
        s.commit()
        return ep.id


_FILM_REPLY = """\
一部 7 秒、9:16 竖屏、写实电影质感的谍战心理对话短片。【人物一致性】苏曼:黑色卷发\
贴近脸侧,眼睛明亮带警惕,五官精致,深宝蓝丝质旗袍,声音柔软清亮。【镜头一|0.0—4.0秒】\
近景,固定机位,她垂眼看戒指。【声音设计】对白逐音节同步,保留茶杯轻碰桌面的声音。\
【影像限制】不要文字水印,不要口型错位。【核心要求】真假难辨的试探,力量来自克制。"""


def test_film_prompt_requires_shots(client):
    """没有分镜就生成:job 明确报错,不产空稿。"""
    headers = _auth(client, "fp_no_shots")
    pid = _create_project(client, headers)
    eid = _seed_episode(pid, with_shots=False)

    r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=headers)
    assert r.status_code == 200, r.text
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "分镜" in job["error"]


def test_film_prompt_generate_and_get(client):
    """生成跑通:原料注入齐活,markdown 围栏被剥掉,结果落库可读。"""
    headers = _auth(client, "fp_gen")
    pid = _create_project(client, headers)
    eid = _seed_episode(pid)
    adapter = _TextAdapter(f"```text\n{_FILM_REPLY}\n```")

    with patch("app.engines.drama.film_prompt.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    got = client.get(f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["film_prompt"] == _FILM_REPLY  # 围栏已剥,整段保存

    prompt = adapter.prompts[0]
    assert "黑色卷发贴近脸侧" in prompt  # 角色卡外貌逐字进原料
    assert "深宝蓝丝质旗袍" in prompt  # 服饰进原料
    assert "苏曼:你为什么还要听?" in prompt  # 台词带说话人(按剧本 lines 反查)
    assert "写实电影质感" in prompt  # 画风锚进原料


def test_film_prompt_save_replaces(client):
    """PUT 整段替换:手改与粘贴自己写的版本都存得下,读取首尾去空白。"""
    headers = _auth(client, "fp_save")
    pid = _create_project(client, headers)
    eid = _seed_episode(pid)

    assert client.get(
        f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=headers
    ).json()["film_prompt"] == ""  # 初始空稿

    body = {"film_prompt": "\n  自己写的整片提示词。\n"}
    r = client.put(f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt",
                   headers=headers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["film_prompt"] == "自己写的整片提示词。"
    assert client.get(
        f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=headers
    ).json()["film_prompt"] == "自己写的整片提示词。"


def test_film_prompt_ownership(client):
    """归属隔离:对他人项目的集读写整片提示词 → 404。"""
    headers = _auth(client, "fp_owner")
    pid = _create_project(client, headers)
    eid = _seed_episode(pid)
    other = _auth(client, "fp_other")

    assert client.get(
        f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt", headers=other
    ).status_code == 404
    assert client.put(
        f"/api/projects/{pid}/drama/episodes/{eid}/film-prompt",
        headers=other, json={"film_prompt": "偷改"},
    ).status_code == 404
