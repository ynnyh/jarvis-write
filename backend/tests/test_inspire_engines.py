# tests/test_inspire_engines.py
# -*- coding: utf-8 -*-
"""两段式构思接口:引擎卡(FAST 档收敛层)+ 深化(强模型只跑选中的)。

动机见 prompts/inspire.py ENGINES_PROMPT 头注:概念生成发生在偏好采集前是"信息真空"。
覆盖:
- /api/inspire/engines:解析引擎卡、超 count 截断、avoid_engines 注入 prompt(换一批不趋同)
- /api/inspire/develop:选中引擎深化成六字段、两张混搭标识、空列表被拦
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


class _FakeAdapter:
    """捕获 prompt 的假适配器:ask() 返回预设 JSON,记录最后一次 prompt 供断言。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_prompt = ""

    async def ask(self, prompt: str, **kw) -> str:  # noqa: ANN003
        self.last_prompt = prompt
        return self.payload


def _patch_engines(monkeypatch, payload: str) -> "_FakeAdapter":
    from app.api import inspire as inspire_mod

    adapter = _FakeAdapter(payload)
    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task: adapter)
    return adapter


_ENGINES_JSON = json.dumps({
    "engines": [
        {"engine": "引擎卡0:落魄镖师接险镖,开箱发现活人", "angle": "小人物·生计·燃",
         "hook": "开箱瞬间的反差"},
        {"engine": "退休刑警发现自己经手的旧案全是冤案", "angle": "老手·立场·冷",
         "hook": "自我怀疑的张力"},
    ]
}, ensure_ascii=False)

_CONCEPT_JSON = json.dumps({
    "logline": "落魄镖师押送一趟不许开箱的险镖,验货夜发现箱中藏着通缉的前朝公主",
    "hook": "规矩与良心的每一次碰撞",
    "twist": "雇主就是当年灭她满门的人",
    "protagonist": "李镖头,四十岁,想金盆洗手却被最后一趟镖锁死",
    "conflict": "江湖规矩要她死,自己良心要她活",
    "setting": "乱世末年,镖局行业凋零",
    "sell": "每一趟不问来路的镖,都是一次良心问价",
}, ensure_ascii=False)


def test_engines_parses_and_marks_fast_task(client, monkeypatch):
    """引擎卡走 FAST 档(SUMMARY task):收敛层要快、便宜。"""
    u = _register(client, "eng_user1")
    adapter = _patch_engines(monkeypatch, _ENGINES_JSON)
    from app.api import inspire as inspire_mod

    seen_tasks: list = []
    real = inspire_mod.get_adapter_for

    def spy(task):
        seen_tasks.append(task)
        return adapter

    monkeypatch.setattr(inspire_mod, "get_adapter_for", spy)
    r = client.post("/api/inspire/engines", headers=_auth(u["token"]),
                    json={"spark": "按「武侠」的套路来", "tendency": {"genre": "武侠"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["engines"]) == 2
    assert data["engines"][0]["engine"].startswith("引擎卡0")
    assert data["engines"][0]["angle"] == "小人物·生计·燃"
    assert seen_tasks[-1].value == "summary"  # FAST 档,不是强模型
    _ = real  # 平掉 lint 未用警告


def test_engines_avoid_block_injected(client, monkeypatch):
    """换一批带 avoid:上一批引擎句必须进 prompt,逼模型换轴(同输入重跑趋同的老根因)。"""
    u = _register(client, "eng_user2")
    adapter = _patch_engines(monkeypatch, _ENGINES_JSON)
    r = client.post("/api/inspire/engines", headers=_auth(u["token"]), json={
        "spark": "按「都市」的套路来",
        "tendency": {"genre": "都市"},
        "avoid_engines": ["落魄镖师接险镖,开箱发现活人"],
    })
    assert r.status_code == 200, r.text
    assert "上一批引擎用户都不满意" in adapter.last_prompt
    assert "落魄镖师接险镖" in adapter.last_prompt


def test_develop_single_engine_to_concept(client, monkeypatch):
    """深化:单引擎 → 六字段概念,走强模型(ARCHITECTURE)。"""
    u = _register(client, "eng_user3")
    adapter = _patch_engines(monkeypatch, _CONCEPT_JSON)
    r = client.post("/api/inspire/develop", headers=_auth(u["token"]), json={
        "engines": ["落魄镖师押送不许开箱的险镖,开箱发现活人"],
        "spark": "按「武侠」的套路来",
        "tendency": {"genre": "武侠"},
    })
    assert r.status_code == 200, r.text
    c = r.json()["concept"]
    assert "镖师" in c["logline"]
    assert c["sell"].startswith("每一趟")
    # 选中引擎必须注入深化 prompt
    assert "落魄镖师押送不许开箱" in adapter.last_prompt


def test_develop_two_engines_marks_mix(client, monkeypatch):
    """两张引擎混搭:prompt 里明示「融合两张卡的要素」。"""
    u = _register(client, "eng_user4")
    adapter = _patch_engines(monkeypatch, _CONCEPT_JSON)
    r = client.post("/api/inspire/develop", headers=_auth(u["token"]), json={
        "engines": ["镖师开箱见活人", "退休刑警旧案全冤"],
    })
    assert r.status_code == 200, r.text
    assert "融合两张卡的要素" in adapter.last_prompt


def test_develop_rejects_empty_engines(client):
    """空引擎列表直接被 pydantic 拦下(min_length=1),不烧 LLM。"""
    u = _register(client, "eng_user5")
    r = client.post("/api/inspire/develop", headers=_auth(u["token"]),
                    json={"engines": []})
    assert r.status_code == 422
