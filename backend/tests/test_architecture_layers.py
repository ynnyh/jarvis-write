# tests/test_architecture_layers.py
# -*- coding: utf-8 -*-
"""架构闸门(确认链 L2)测试:逐层生成/拍板/撤回级联/带话重出/手改作废(TestClient + mock LLM)。

验证点:
- 链式约束:上游未拍板时,下游层生成 → 409、拍板 → 409
- 逐层生成:产物落库、该层与下游回未拍板;整本链路(信任模式)零回归
- 拍板/撤回:撤回上游级联作废下游;上游拍板态不受影响
- 带话重出:directive 进 prompt;重出后该层回未拍板
- 手改(PATCH):该层与下游回未拍板
- 存量兼容:legacy 整本生成的架构 confirmed_layers=NULL(前端按全层已认解释)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_pipeline import MockAdapter

INVITE = "test-invite"

REPLY_SEED = "当落魄乐手陈默捡到能听见亡妻歌声的旧提琴,必须在琴声消散前找出真相,否则记忆永失。"
REPLY_CHARS = "[主角]陈默:背景创伤:亡妻骤逝……深层渴望:再见一面。"
REPLY_WORLD = "1. 物理维度:滨海老城与琴行巷……法则体系:琴声只在雨夜响起。"
REPLY_PLOT = "第一幕(第1-3章):陈默买下旧提琴,琴声初响……"


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


def _create_project(client: TestClient, headers: dict, title: str = "闸门测试书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title, "target_chapters": 3})
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


def _gen_layer(client: TestClient, headers: dict, pid: int, layer: str,
               directive: str = "") -> dict:
    """逐层生成一层(带人物画像 mock),返回 job。"""
    with patch("app.api.projects.architecture.extract_cast_profiles", new=AsyncMock()):
        r = client.post(
            f"/api/projects/{pid}/architecture/layer-async",
            headers=headers,
            json={"layer": layer, "tendency": {}, "directive": directive},
        )
        assert r.status_code == 200, r.text
        return _wait_job(client, headers, r.json()["job_id"])


def _confirm(client: TestClient, headers: dict, pid: int, layer: str, confirmed: bool):
    return client.post(
        f"/api/projects/{pid}/architecture/confirm",
        headers=headers,
        json={"layer": layer, "confirmed": confirmed},
    )


def _layers(client: TestClient, headers: dict, pid: int) -> dict:
    r = client.get(f"/api/projects/{pid}/architecture", headers=headers)
    assert r.status_code == 200, r.text
    return r.json().get("confirmed_layers") or {}


def test_layer_rejects_bad_key_and_unconfirmed_upstream(client):
    """非法层 key → 400;上游未拍板时下游生成 → 409、拍板 → 409。"""
    headers = _auth(client, "gate_guard_user")
    p = _create_project(client, headers, "闸门守卫书")

    r = client.post(
        f"/api/projects/{p['id']}/architecture/layer-async",
        headers=headers, json={"layer": "bogus", "tendency": {}},
    )
    assert r.status_code == 400

    # 无架构 = 全层未拍板:核心种子可以生成,角色动力学不行
    r = client.post(
        f"/api/projects/{p['id']}/architecture/layer-async",
        headers=headers, json={"layer": "character_dynamics", "tendency": {}},
    )
    assert r.status_code == 409, r.text
    assert "核心种子" in r.json()["detail"]


def test_full_gate_flow(client):
    """种子→拍板→角色→拍板→世界观→情节:链式放行,拍板态逐层落库。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "gate_flow_user")
    p = _create_project(client, headers, "闸门流通书")
    pid = p["id"]

    adapter = MockAdapter([REPLY_SEED, REPLY_CHARS, REPLY_WORLD, REPLY_PLOT])
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter), \
         patch("app.api.projects.architecture.extract_cast_profiles", new=AsyncMock()):
        # 种子层
        job = _gen_layer(client, headers, pid, "core_seed")
        assert job["status"] == "done", job
        assert job["result"]["core_seed"] == REPLY_SEED
        state = _layers(client, headers, pid)
        assert state["core_seed"] is False
        assert state["plot_architecture"] is False

        # 上游未拍板,拍板角色层 → 409
        r = _confirm(client, headers, pid, "character_dynamics", True)
        assert r.status_code == 409

        # 拍板种子 → True;生成角色层(吃已拍板种子)→ 落库且回未拍板
        assert _confirm(client, headers, pid, "core_seed", True).status_code == 200
        assert _layers(client, headers, pid)["core_seed"] is True

        job = _gen_layer(client, headers, pid, "character_dynamics")
        assert job["status"] == "done", job
        assert job["result"]["character_dynamics"] == REPLY_CHARS
        state = _layers(client, headers, pid)
        assert state["core_seed"] is True  # 上游拍板不受影响
        assert state["character_dynamics"] is False

        assert _confirm(client, headers, pid, "character_dynamics", True).status_code == 200

        # 世界观、情节两层连发(默认连跑语义:两次调用间无强制停)
        job = _gen_layer(client, headers, pid, "world_building")
        assert job["status"] == "done", job
        assert _confirm(client, headers, pid, "world_building", True).status_code == 200
        job = _gen_layer(client, headers, pid, "plot_architecture")
        assert job["status"] == "done", job
        assert _confirm(client, headers, pid, "plot_architecture", True).status_code == 200

    state = _layers(client, headers, pid)
    assert state == {
        "core_seed": True, "character_dynamics": True,
        "world_building": True, "plot_architecture": True,
    }
    # 四层各自一次 LLM 调用(不是整本四发一口气)
    assert len(adapter.calls) == 4


def test_unconfirm_cascades_downstream(client):
    """撤回角色层 → 世界观/情节一并回未拍板;种子拍板态保持。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "gate_unconf_user")
    p = _create_project(client, headers, "闸门级联书")
    pid = p["id"]

    with patch.object(arch_mod, "get_adapter_for",
                      return_value=MockAdapter([REPLY_SEED, REPLY_CHARS, REPLY_WORLD, REPLY_PLOT])), \
         patch("app.api.projects.architecture.extract_cast_profiles", new=AsyncMock()):
        for layer in ("core_seed", "character_dynamics", "world_building", "plot_architecture"):
            assert _gen_layer(client, headers, pid, layer)["status"] == "done"
            assert _confirm(client, headers, pid, layer, True).status_code == 200

        # 撤回角色层 → 下游级联撤回
        assert _confirm(client, headers, pid, "character_dynamics", False).status_code == 200
        state = _layers(client, headers, pid)
        assert state["core_seed"] is True
        assert state["character_dynamics"] is False
        assert state["world_building"] is False
        assert state["plot_architecture"] is False

        # 撤回后重新拍板角色层:上游种子已认,可以;下游未重出,仍 False
        assert _confirm(client, headers, pid, "character_dynamics", True).status_code == 200
        assert _layers(client, headers, pid)["world_building"] is False


def test_layer_regenerate_with_directive_resets_confirmation(client):
    """带话重出种子层:directive 进 prompt;重出后该层与下游回未拍板。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "gate_regen_user")
    p = _create_project(client, headers, "闸门带话书")
    pid = p["id"]

    adapter = MockAdapter([REPLY_SEED, REPLY_SEED + " 琴声必须在三十天内找到主人。"])
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter), \
         patch("app.api.projects.architecture.extract_cast_profiles", new=AsyncMock()):
        assert _gen_layer(client, headers, pid, "core_seed")["status"] == "done"
        assert _confirm(client, headers, pid, "core_seed", True).status_code == 200

        job = _gen_layer(client, headers, pid, "core_seed", directive="加一条三十天倒计时")
        assert job["status"] == "done", job

    # directive 真的进了第二次 prompt
    assert len(adapter.calls) == 2
    assert "三十天倒计时" in adapter.calls[1]
    state = _layers(client, headers, pid)
    assert state["core_seed"] is False  # 重出后须重新拍板


def test_manual_edit_resets_downstream(client):
    """PATCH 手改核心种子 → 种子与下游全部回未拍板。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "gate_edit_user")
    p = _create_project(client, headers, "闸门手改书")
    pid = p["id"]

    with patch.object(arch_mod, "get_adapter_for",
                      return_value=MockAdapter([REPLY_SEED, REPLY_CHARS, REPLY_WORLD, REPLY_PLOT])), \
         patch("app.api.projects.architecture.extract_cast_profiles", new=AsyncMock()):
        for layer in ("core_seed", "character_dynamics"):
            assert _gen_layer(client, headers, pid, layer)["status"] == "done"
            assert _confirm(client, headers, pid, layer, True).status_code == 200

    r = client.patch(
        f"/api/projects/{pid}/architecture",
        headers=headers,
        json={"core_seed": "手改后的种子:主角换成女性琴师。"},
    )
    assert r.status_code == 200, r.text
    state = _layers(client, headers, pid)
    assert state["core_seed"] is False
    assert state["character_dynamics"] is False


def test_legacy_generation_leaves_layers_null(client):
    """legacy 整本生成(信任模式链路):confirmed_layers=NULL,旧行为零变化。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "gate_legacy_user")
    p = _create_project(client, headers, "闸门旧链书")

    with patch.object(arch_mod, "get_adapter_for",
                      return_value=MockAdapter([REPLY_SEED, REPLY_CHARS, REPLY_WORLD, REPLY_PLOT])):
        r = client.post(
            f"/api/projects/{p['id']}/architecture-async",
            headers=headers, json={"tendency": {}},
        )
        assert r.status_code == 200
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job

    r = client.get(f"/api/projects/{p['id']}/architecture", headers=headers)
    assert r.status_code == 200
    assert r.json()["confirmed_layers"] is None  # 前端按全层已认解释
