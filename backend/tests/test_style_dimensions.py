# tests/test_style_dimensions.py
# -*- coding: utf-8 -*-
"""文风画像(docs/20 同批「文风可视化+进化」):
- 渲染:空画像零字节(存量书 prompt 不变);有内容渲染六维指令块
- API:GET/PUT/历史/回退;保存合并语义(空白维度不清已有内容)
- 续集分析:结构化画像写 style_profile 列(带来源标注)
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.engines.style_profile import (
    normalize_profile,
    profile_from_analysis,
    render_style_profile_block,
)
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


def _mkproject(client: TestClient, headers: dict) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": "画像之书", "target_chapters": 6, "genre": "都市"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestProfileRender:
    def test_empty_profile_renders_nothing(self):
        """空画像零字节:存量书没有画像,prompt 字节级不变。"""
        assert render_style_profile_block(None) == ""
        assert render_style_profile_block({"dims": {}}) == ""
        assert render_style_profile_block({"dims": {k: {"text": ""} for k in
                                                    ["perspective", "rhythm", "dialogue",
                                                     "rhetoric", "mood", "hook"]}}) == ""

    def test_render_includes_dims_and_label(self):
        profile = profile_from_analysis({"style_profile": {
            "perspective": "第三人称限知,跟男主",
            "hook": "章末必留钩,悬念不过夜",
        }})
        block = render_style_profile_block(profile)
        assert "文风画像" in block
        assert "叙事视角:第三人称限知,跟男主" in block
        assert "起势与钩法:章末必留钩,悬念不过夜" in block
        # 未填维度不出现
        assert "对话密度" not in block

    def test_normalize_tolerates_dirty_shapes(self):
        p = normalize_profile({"dims": {"perspective": "直接给字符串", "rhythm": {"text": "短句"}},
                               "version": "2"})
        assert p["dims"]["perspective"]["text"] == "直接给字符串"
        assert p["dims"]["rhythm"]["text"] == "短句"
        assert p["version"] == 2


class TestProfileApi:
    def test_save_history_and_restore(self, client: TestClient):
        h = _auth(client, f"prof-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h)

        # 初始:画像不存在
        r = client.get(f"/api/projects/{pid}/style-dimensions", headers=h)
        assert r.status_code == 200
        assert r.json()["version"] == 0

        # 第一次保存:v1
        r = client.put(f"/api/projects/{pid}/style-dimensions", headers=h, json={
            "dims": {"perspective": "第三人称限知", "hook": "章末留钩"},
        })
        assert r.json()["version"] == 1
        assert r.json()["dims"]["perspective"]["source"] == "手改"

        # 第二次保存:只改 hook——perspective 不被清掉(合并语义),v1 进历史
        r = client.put(f"/api/projects/{pid}/style-dimensions", headers=h, json={
            "dims": {"hook": "悬念不过夜"},
        })
        out = r.json()
        assert out["version"] == 2
        assert out["dims"]["perspective"]["text"] == "第三人称限知"
        assert out["dims"]["hook"]["text"] == "悬念不过夜"
        assert len(out["history"]) == 1 and out["history"][0]["version"] == 1

        # 回退到 v1:当前 v2 进历史,版本+1
        r = client.post(f"/api/projects/{pid}/style-dimensions/history/1/restore", headers=h)
        assert r.status_code == 200
        out = r.json()
        assert out["version"] == 3
        assert out["dims"]["hook"]["text"] == "章末留钩"  # v1 的内容回来了
        assert len(out["history"]) == 2

        # 不存在的历史版本 → 404
        r = client.post(f"/api/projects/{pid}/style-dimensions/history/99/restore", headers=h)
        assert r.status_code == 404
