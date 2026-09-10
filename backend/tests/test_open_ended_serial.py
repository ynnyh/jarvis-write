# tests/test_open_ended_serial.py
# -*- coding: utf-8 -*-
"""开放式连载(结局未定)模式。

覆盖:
- Project.open_ended 建/查/改全链路(契约式项目默认 False,行为不变);
- 架构 Step4 按模式二选一:连载式走 PLOT_ARCHITECTURE_OPEN_PROMPT(无第三幕/终局),
  契约式仍走原 prompt(评测基线零影响);
- 蓝图铺满后「展开下一卷」:契约式 400(旧行为);连载式自动续订
  (体量 +30、卷纲按新体量+前情重出、续着铺蓝图)。
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_pipeline import MOCK_ARCH_REPLIES, MOCK_BLUEPRINT_REPLY, MockAdapter

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


def _create_project(client: TestClient, headers: dict, **extra) -> dict:
    r = client.post("/api/projects", headers=headers,
                    json={"title": "连载测试书", "target_chapters": 3, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时未完成: {job}"
        time.sleep(0.02)


def test_open_ended_flag_roundtrip(client):
    headers = _auth(client, "open_flag_user")
    p = _create_project(client, headers, open_ended=True)
    assert p["open_ended"] is True

    # 默认(不传)= 契约式 False,存量行为零变化
    p2 = _create_project(client, headers, title="契约书")
    assert p2["open_ended"] is False

    # 创建后可切换
    r = client.patch(f"/api/projects/{p2['id']}", headers=headers, json={"open_ended": True})
    assert r.status_code == 200 and r.json()["open_ended"] is True
    r = client.patch(f"/api/projects/{p2['id']}", headers=headers, json={"open_ended": False})
    assert r.json()["open_ended"] is False


def _seed_architecture(client, headers, pid: int) -> list[str]:
    """mock 架构四步,返回第 4 步(情节架构)的 prompt。"""
    from app.engines.pipeline import architecture as arch_mod

    adapter = MockAdapter(MOCK_ARCH_REPLIES)
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/architecture", headers=headers, json={"tendency": {}})
        assert r.status_code == 200, r.text
    assert len(adapter.calls) == 4
    return adapter.calls[3]


def test_architecture_prompt_variants(client):
    headers = _auth(client, "open_arch_user")

    # 契约式:原三幕 prompt 不动(评测基线指纹不变)
    contract = _create_project(client, headers, title="契约架构书")
    plot_prompt = _seed_architecture(client, headers, contract["id"])
    assert "第三幕(结局" in plot_prompt
    assert "不规划全书终局" not in plot_prompt

    # 连载式:走开放式变体——有长线引擎与本批次收束点,无第三幕/全书终局
    opened = _create_project(client, headers, title="连载架构书", open_ended=True)
    open_prompt = _seed_architecture(client, headers, opened["id"])
    assert "不规划全书终局" in open_prompt
    assert "长线引擎" in open_prompt and "本批次收束点" in open_prompt
    assert "第三幕(结局" not in open_prompt


# 续订时卷纲的 mock 回复:目标 3+30=33 章 → 2 段(第一段只到 5,展开范围恰好够 mock 回复)
_MACRO_PLAN_JSON = json.dumps({
    "segments": [
        {"start": 1, "end": 5, "goal": "旧走向相容:林晚巩固芯片人格的信任,企业线收紧。"},
        {"start": 6, "end": 33, "goal": "本批次收束点:悬念半解、留最大钩子(非全书结局)。"},
    ]
}, ensure_ascii=False)

# 续订后的展开蓝图:第 4-5 章(缺 6-15 只产生警告,不失败)
_RENEW_CHAPTERS_REPLY = """\
第4章 - 芯片的低语
本章定位:常规推进
核心作用:深入芯片秘密
悬念密度:中
伏笔操作:强化:神秘芯片的来历
认知颠覆:★★☆☆☆
涉及人物:林晚,K
关键道具:神秘芯片
场景地点:安全屋
本章简述:林晚在安全屋解析芯片,听见人格的低语。

第5章 - 第二个影子
本章定位:关键转折
核心作用:揭示被跟踪
悬念密度:高
伏笔操作:埋设:第二个影子
认知颠覆:★★★☆☆
涉及人物:林晚,老周
关键道具:无
场景地点:旧城区
本章简述:林晚发现有人先一步调查芯片,影子逼近。
"""


def _seed_full_book(client, headers, pid: int) -> None:
    """铺架构 + 全量蓝图(target_chapters=3,mock 下 1 次调用出 3 章)。"""
    _seed_architecture(client, headers, pid)
    from app.engines.pipeline import blueprint as bp_mod

    with patch.object(bp_mod, "get_adapter_for", return_value=MockAdapter([MOCK_BLUEPRINT_REPLY])):
        r = client.post(f"/api/projects/{pid}/blueprint", headers=headers, json={"tendency": {}})
        assert r.status_code == 200, r.text
        assert len(r.json()["outlines"]) == 3


def test_blueprint_extend_renews_serial(client):
    """连载式:铺满当前批次后再展开 → 自动续订 30 章,卷纲带前情重出,蓝图续铺。"""
    headers = _auth(client, "open_renew_user")
    p = _create_project(client, headers, open_ended=True)
    _seed_full_book(client, headers, p["id"])

    from app.api.projects import blueprint as bp_api
    from app.engines.pipeline import blueprint as bp_mod

    adapter = MockAdapter([_MACRO_PLAN_JSON, _RENEW_CHAPTERS_REPLY])
    # 两个模块都要打补丁:卷纲走 api 层的 get_adapter_for,蓝图走引擎层的
    with patch.object(bp_api, "get_adapter_for", return_value=adapter), \
         patch.object(bp_mod, "get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{p['id']}/blueprint-extend-async", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["result"]["renewed_to"] == 33  # 3 + 30
    assert job["result"]["planned_range"] == [4, 5]  # 新卷纲第一段覆盖到 5

    # 体量顺延落库;卷纲按新体量重出(2 段);蓝图续铺 4-5 章
    fresh = client.get(f"/api/projects/{p['id']}", headers=headers).json()
    assert fresh["target_chapters"] == 33
    assert fresh["open_ended"] is True
    assert len(fresh["macro_plan"]) == 2

    # 连载式 hard_note 注入卷纲 prompt:结局留白 + 前情相容
    macro_prompt = adapter.calls[0]
    assert "开放式连载" in macro_prompt and "不是全书结局" in macro_prompt
    assert "第 1-3 章蓝图已成文" in macro_prompt

    outlines = client.get(f"/api/projects/{p['id']}/outlines", headers=headers).json()
    assert [o["chapter_number"] for o in outlines] == [1, 2, 3, 4, 5]


def test_blueprint_extend_contract_still_blocked(client):
    """契约式:铺满后展开仍 400(旧行为),不许静默续。"""
    headers = _auth(client, "contract_renew_user")
    p = _create_project(client, headers)  # open_ended 默认 False
    _seed_full_book(client, headers, p["id"])

    r = client.post(f"/api/projects/{p['id']}/blueprint-extend-async", headers=headers)
    assert r.status_code == 400
    assert "铺满" in r.json()["detail"]


def test_next_goal_text_open_ended():
    """展开「当前批次最后一段」时的下一卷预告:连载式=收束点留钩子,契约式=收束全书。"""
    from app.api.projects.blueprint import _next_goal_text

    # 有下一段:直接用卷纲里的卷目标,两种模式一致
    assert _next_goal_text({"goal": "中段卷目标"}, open_ended=True) == "中段卷目标"
    # 无下一段(本批次末段):契约式收束全书;连载式不许写结局
    assert _next_goal_text(None, open_ended=False) == "(已是最终卷,收束全书)"
    serial = _next_goal_text(None, open_ended=True)
    assert "不是全书结局" in serial and "收束点" in serial
