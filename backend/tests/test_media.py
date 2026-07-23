# tests/test_media.py
# -*- coding: utf-8 -*-
"""周边创作接口测试(封面 / 主题曲提示词,TestClient + mock LLM)。

验证点:
- POST .../cover/generate、.../anthem/generate 立即返回 job_id,轮询完成结果结构正确
- 无主题(topic 空)→ 400
- 归属隔离:对他人项目发起 → 404
- normalize:covers 最多 3 条、缺提示词的方案丢弃
"""
from __future__ import annotations

import json
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


def _create_project(client: TestClient, headers: dict, title: str = "周边书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _set_topic(client: TestClient, headers: dict, pid: int) -> None:
    r = client.patch(
        f"/api/projects/{pid}", headers=headers,
        json={"topic": "落魄镖师发现镖箱里藏着个大活人", "genre": "武侠"},
    )
    assert r.status_code == 200, r.text


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


class _JsonAdapter:
    """假适配器:ask() 恒返回给定 JSON 字符串。"""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload, ensure_ascii=False)

    async def ask(self, prompt, system=None):
        return self._raw


_COVER_REPLY = {
    "covers": [
        {"style": "国风工笔", "prompt_cn": "一位镖师立于风雪古道", "prompt_en": "wuxia escort, snowy road", "negative": "文字,水印"},
        {"style": "电影写实", "prompt_cn": "逆光剪影,暖金夕照", "prompt_en": "backlit silhouette", "negative": "多余肢体"},
        {"style": "水墨抽象", "prompt_cn": "泼墨山水,一点朱红", "prompt_en": "ink wash landscape", "negative": "低分辨率"},
        {"style": "多余的第四套", "prompt_cn": "应被裁掉", "prompt_en": "dropped", "negative": ""},
        {"style": "无提示词方案", "prompt_cn": "", "prompt_en": "", "negative": "x"},
    ]
}

_ANTHEM_REPLY = {
    "song_title": "风雪镖歌",
    "style_tags": "cinematic, guzheng folk, heroic, male vocal, driving beat",
    "lyrics": "[Verse 1]\n风雪压弯了刀\n[Chorus]\n镖旗不倒",
    "vibe": "苍凉又不失热血,呼应镖师的孤勇。",
}


def test_cover_generate_full_flow(client):
    """封面异步生成:job 完成、covers 结构正确、最多 3 条且无提示词方案被丢弃。"""
    headers = _auth(client, "cover_user")
    other = _auth(client, "cover_other")
    p = _create_project(client, headers)
    _set_topic(client, headers, p["id"])

    with patch("app.api.media.create_llm_adapter", return_value=_JsonAdapter(_COVER_REPLY)):
        r = client.post(f"/api/projects/{p['id']}/cover/generate", headers=headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        # 归属隔离:对他人项目发起 → 404
        assert client.post(
            f"/api/projects/{p['id']}/cover/generate", headers=other
        ).status_code == 404
        job = _wait_job(client, headers, job_id)

    assert job["status"] == "done", job
    assert job["kind"] == f"cover-{p['id']}"
    covers = job["result"]["covers"]
    assert len(covers) == 3  # 第 4 套超量裁掉,第 5 套无提示词丢弃
    assert covers[0]["style"] == "国风工笔"
    assert covers[0]["prompt_cn"] == "一位镖师立于风雪古道"


def test_anthem_generate_full_flow(client):
    """主题曲异步生成:job 完成、四字段结构正确。"""
    headers = _auth(client, "anthem_user")
    p = _create_project(client, headers, "主题曲书")
    _set_topic(client, headers, p["id"])

    with patch("app.api.media.create_llm_adapter", return_value=_JsonAdapter(_ANTHEM_REPLY)):
        r = client.post(f"/api/projects/{p['id']}/anthem/generate", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["kind"] == f"anthem-{p['id']}"
    res = job["result"]
    assert res["song_title"] == "风雪镖歌"
    assert "guzheng folk" in res["style_tags"]
    assert "[Chorus]" in res["lyrics"]
    assert res["vibe"]


def test_cover_requires_topic(client):
    """未定主题 → 400,提示先去概念定主题。"""
    headers = _auth(client, "cover_notopic")
    p = _create_project(client, headers, "没主题封面书")
    r = client.post(f"/api/projects/{p['id']}/cover/generate", headers=headers)
    assert r.status_code == 400
    assert "主题" in r.json()["detail"]


def test_anthem_requires_topic(client):
    """未定主题 → 400。"""
    headers = _auth(client, "anthem_notopic")
    p = _create_project(client, headers, "没主题曲书")
    r = client.post(f"/api/projects/{p['id']}/anthem/generate", headers=headers)
    assert r.status_code == 400
    assert "主题" in r.json()["detail"]
