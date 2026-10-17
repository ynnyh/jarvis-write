# tests/test_intent.py
# -*- coding: utf-8 -*-
"""理解卡:回读(不落库)+ 确认①(落卡/显式题材/渲染订单/启动样稿链)。

守的是什么(docs/29 的病根):题材**不许**由一次廉价推断静默写进项目;世界坐标
必须随作者确认显式落库,并成为全链硬约束。这两条一旦回退,「真实历史被写成架空」
的老毛病就会原样复发。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"

_READBACK_JSON = json.dumps(
    {
        "world": "真实历史·西汉末年(可想象细节,不得架空)",
        "viewpoint": "第一人称·传国玉玺自述",
        "cast": "传国玉玺本身(非人视角)",
        # 重复项 + 空项:收敛应去重去空
        "flavor": ["苍凉", "史感", "苍凉", ""],
        "must": ["真实史实不得改名换姓"],
        "avoid": ["架空历史", "穿越"],
        "genre": "历史·物语体",
        "uncertain": [
            {"field": "world", "reason": "朝代可能有出入",
             "candidates": ["西汉末年", "东汉末年", "新莽"]},
            # 未知字段必须被丢掉(白名单)
            {"field": "bogus", "candidates": ["x"]},
        ],
    },
    ensure_ascii=False,
)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project(client: TestClient, headers: dict, title: str = "玉玺传") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


class _JsonAdapter:
    """只实现 ask 的最小适配器(回读端点只用 ask)。"""

    def __init__(self, text: str):
        self._text = text
        self.last_prompt = ""

    async def ask(self, prompt, system=None):
        self.last_prompt = prompt
        return self._text


# ---------- 纯函数:理解卡渲染 ----------


def test_render_intent_block_shape():
    """理解卡 → 注入块:坐标字段在前、带铁律;空卡返回空串(老项目零影响)。"""
    from app.engines.intent import render_intent_block

    class _P:
        intent_card = {
            "world": "真实历史·西汉末年", "viewpoint": "第一人称·玉玺自述",
            "cast": "传国玉玺", "flavor": ["苍凉", "史感"], "must": [],
            "avoid": ["架空历史"], "genre": "历史·物语体", "uncertain": [],
        }

    block = render_intent_block(_P())
    assert "世界坐标:真实历史·西汉末年" in block
    assert "题材:历史·物语体" in block
    assert "铁律" in block and "坐标级设定" in block

    class _Empty:
        intent_card = None

    assert render_intent_block(_Empty()) == ""


def test_clean_intent_card_whitelists_and_dedupes():
    from app.engines.intent import clean_intent_card, intent_card_empty

    card = clean_intent_card(json.loads(_READBACK_JSON))
    assert card["flavor"] == ["苍凉", "史感"]
    assert [u["field"] for u in card["uncertain"]] == ["world"]
    assert set(card) == {
        "world", "viewpoint", "cast", "flavor", "must", "avoid", "genre", "uncertain",
    }
    assert intent_card_empty(clean_intent_card({})) is True
    assert intent_card_empty(card) is False


# ---------- 回读:不落库 ----------


def test_readback_returns_card_without_touching_project(client):
    headers = _auth(_register(client, "intent_read")["token"])
    pid = _project(client, headers)
    adapter = _JsonAdapter(_READBACK_JSON)
    with patch("app.api.projects.intent.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{pid}/intent-readback",
            headers=headers,
            json={"mode": "serial", "topic": "我想写传国玉玺的自传,真实历史,别架空",
                  "genre": "", "feedback": "", "avoid": []},
        )
    assert r.status_code == 200, r.text
    card = r.json()["card"]
    assert card["world"].startswith("真实历史")
    assert card["flavor"] == ["苍凉", "史感"]
    assert [u["field"] for u in card["uncertain"]] == ["world"]
    assert "传国玉玺" in adapter.last_prompt  # 点子进了 prompt

    # 关键:回读只是草稿,**不写项目**(题材不许被静默覆盖)
    from app.db.models import Project
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        project = db.get(Project, pid)
        assert project.genre == ""
        assert project.intent_card is None


def test_readback_passes_feedback_into_prompt(client):
    headers = _auth(_register(client, "intent_fb")["token"])
    pid = _project(client, headers)
    adapter = _JsonAdapter(_READBACK_JSON)
    with patch("app.api.projects.intent.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{pid}/intent-readback",
            headers=headers,
            json={"mode": "serial", "topic": "玉玺自传",
                  "feedback": "不要架空,就是真实历史", "avoid": ["穿越"]},
        )
    assert r.status_code == 200
    assert "不要架空,就是真实历史" in adapter.last_prompt
    assert "穿越" in adapter.last_prompt


def test_readback_requires_topic(client):
    headers = _auth(_register(client, "intent_notopic")["token"])
    pid = _project(client, headers)
    r = client.post(
        f"/api/projects/{pid}/intent-readback",
        headers=headers,
        json={"mode": "serial", "topic": "", "genre": "", "feedback": "", "avoid": []},
    )
    assert r.status_code == 400
    assert "点子" in r.json()["detail"]


def test_readback_bad_model_output_502(client):
    headers = _auth(_register(client, "intent_bad")["token"])
    pid = _project(client, headers)
    with patch("app.api.projects.intent.get_adapter_for",
               return_value=_JsonAdapter("这不是 JSON")):
        r = client.post(
            f"/api/projects/{pid}/intent-readback",
            headers=headers,
            json={"mode": "serial", "topic": "玉玺自传", "genre": "", "feedback": "", "avoid": []},
        )
    assert r.status_code == 502


# ---------- 确认①:落卡 + 订单 + 样稿链 ----------


def _seed_plan(pid: int) -> None:
    from app.db.models import Project
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.book_plans = [{
            "title": "受命于天", "kernel": "玉玺看尽两千年", "protagonist": "传国玉玺",
            "world": "真实历史", "arc": "从和氏璧到失踪", "engine": "每朝一劫",
            "scale": "中篇", "flavor": ["苍凉"], "label": "物语体",
        }]
        db.commit()


def test_confirm_locks_card_and_starts_sample_chain(client):
    from app.db.models import Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "intent_confirm")["token"])
    pid = _project(client, headers)
    _seed_plan(pid)

    with patch("app.api.projects.intent.spawn_sample_chain", return_value="job-sample") as spawned:
        r = client.post(
            f"/api/projects/{pid}/intent-confirm",
            headers=headers,
            json={
                "card": {"world": "真实历史·西汉末年", "viewpoint": "第一人称·玉玺",
                         "cast": "传国玉玺本身", "flavor": ["苍凉"],
                         "must": ["真实史实不改名"], "avoid": ["架空历史"],
                         "genre": "历史·物语体", "uncertain": []},
                "mode": "serial",
                "index": 0,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sample_job_id"] == "job-sample"
    assert spawned.call_count == 1
    project = body["project"]
    assert project["genre"] == "历史·物语体"          # 题材显式落库
    assert project["brief_confirmed"] is True
    assert project["setup_state"] == "sample"
    assert "受命于天" in project["brief"]

    with SessionLocal() as db:
        row = db.get(Project, pid)
        assert row.intent_card["world"] == "真实历史·西汉末年"
        assert row.intent_card["avoid"] == ["架空历史"]
        # 方向卡推荐档(中篇)落到体量
        assert row.target_chapters == 60 and row.target_words_per_chapter == 3000


def test_confirm_accepts_card_without_plan_when_order_exists(client):
    """作者可能不改方向:订单已在库(brief 已拍板过),只落卡也能过。"""
    from app.db.models import Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "intent_noplan")["token"])
    pid = _project(client, headers)
    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "书名(暂定):《玉玺》\n【故事内核】物语体自述"
        row.brief_confirmed = True
        db.commit()

    with patch("app.api.projects.intent.spawn_sample_chain", return_value="j2"):
        r = client.post(
            f"/api/projects/{pid}/intent-confirm",
            headers=headers,
            json={"card": {"world": "真实历史", "cast": "玉玺", "genre": "历史"},
                  "mode": "serial"},
        )
    assert r.status_code == 200, r.text


def test_confirm_rejects_empty_card_and_missing_order(client):
    headers = _auth(_register(client, "intent_guard")["token"])
    pid = _project(client, headers)

    r = client.post(
        f"/api/projects/{pid}/intent-confirm",
        headers=headers,
        json={"card": {"world": "  ", "flavor": [], "must": [], "avoid": []}, "mode": "serial"},
    )
    assert r.status_code == 400 and "理解卡" in r.json()["detail"]

    # 卡有内容但既没选方向卡、库里也没有订单 → 400
    r = client.post(
        f"/api/projects/{pid}/intent-confirm",
        headers=headers,
        json={"card": {"world": "真实历史", "genre": "历史"}, "mode": "serial"},
    )
    assert r.status_code == 400 and "方向卡" in r.json()["detail"]


def test_confirm_rejects_out_of_range_plan_index(client):
    headers = _auth(_register(client, "intent_oob")["token"])
    pid = _project(client, headers)
    _seed_plan(pid)
    r = client.post(
        f"/api/projects/{pid}/intent-confirm",
        headers=headers,
        json={"card": {"world": "真实历史", "genre": "历史"}, "mode": "serial", "index": 5},
    )
    assert r.status_code == 400 and "越界" in r.json()["detail"]
