# tests/test_pattern_custom_and_drama_plan.py
"""自定义故事骨架 + 漫剧目标集数。

覆盖:
- PATTERN_DERIVE 接口:AI 反推概念 → 四件套配方(假适配器,不发真请求);
  解析失败 502 不降级——配方是整本书的骨架,半截的不能糊弄;
- pattern_from_derived 收敛:缺字段兜底,宁短勿脏;
- StoryDNA.pattern_custom 注入:pattern_key 优先,custom 兜底,双空为空串;
- PlanIn.target_episodes:0 透传为空 target_block(旧行为),>0 注入硬约束行。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.api import inspire as inspire_mod
from app.api.drama._common import PlanIn
from app.engines.tendency.assembler import dna_block_of
from app.main import app
from app.prompts.story_patterns import PATTERN_DERIVE_PROMPT, pattern_from_derived
from app.schemas.dna import StoryDNA

INVITE = "test-invite"

_DERIVED_JSON = json.dumps({
    "name": "异国追梦·阶层恋",
    "formula": "小人物闯进顶级圈层,阶层落差是引擎",
    "rhythm": "第1章被奚落;每章一次露一手;每2章撞一次阶层墙;章末卡破冰",
    "beats": ["行李箱被嘲笑", "大小姐出手相护", "用土办法解决圈内难题"],
    "opener": "练习生宿舍的电梯要刷卡。",
}, ensure_ascii=False)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_pattern_from_derived_fills_and_drops_empty():
    text = pattern_from_derived(json.loads(_DERIVED_JSON))
    assert "骨架配方:小人物闯进顶级圈层" in text
    assert "必备桥段" in text and "大小姐出手相护" in text
    assert "开场示范" in text
    # 全空数据 → 空串(宁短勿脏,不给 prompt 塞空行标题)
    assert pattern_from_derived({}) == ""
    assert pattern_from_derived({"formula": "", "rhythm": "", "beats": []}) == ""


def test_dna_block_custom_vs_key_priority():
    key_block = dna_block_of(StoryDNA(pattern_key="face_slap"))
    assert "故事骨架" in key_block and "打脸" in key_block

    # 未选精选 → custom 兜底注入
    custom = dna_block_of(StoryDNA(pattern_custom="骨架配方:快递员修仙系统流\n节奏规则:每章2钩"))
    assert "故事骨架" in custom and "快递员修仙" in custom

    # 双选:精选优先,custom 不注入(避免两套配方打架)
    both = dna_block_of(StoryDNA(pattern_key="face_slap", pattern_custom="骨架配方:另一套"))
    assert "另一套" not in both and "打脸" in both

    # 双空 → 空串(基线不变)
    assert dna_block_of(StoryDNA()) == ""


def test_pattern_derive_endpoint(client, monkeypatch):
    r = client.post("/api/auth/register", json={
        "username": "pat_derive1", "password": "pass123", "invite_code": INVITE})
    assert r.status_code == 200
    headers = _auth(r.json()["token"])

    class _FakeAdapter:
        async def ask(self, prompt):
            assert "山东小伙" in prompt or "text" in PATTERN_DERIVE_PROMPT
            return _DERIVED_JSON

    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task: _FakeAdapter())
    resp = client.post("/api/inspire/dna/pattern-derive", headers=headers,
                       json={"text": "山东小伙去韩国做练习生,认识财阀大小姐"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "异国追梦·阶层恋"
    assert "骨架配方" in body["custom"] and len(body["beats"]) == 3

    # 校验类 JSON 任务:解析失败显式 502,不降级
    class _BadAdapter:
        async def ask(self, prompt):
            return '{"name": "截断一半'

    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task: _BadAdapter())
    resp = client.post("/api/inspire/dna/pattern-derive", headers=headers,
                       json={"text": "末日囤货流,主角提前布局躺赢"})
    assert resp.status_code == 502

    # 输入太短直接 422(pydantic min_length)
    resp = client.post("/api/inspire/dna/pattern-derive", headers=headers, json={"text": "短"})
    assert resp.status_code == 422


def test_plan_in_target_episodes():
    # 默认 0 = AI 自定(旧行为)
    body = PlanIn(from_chapter=1, to_chapter=10)
    assert body.target_episodes == 0
    body = PlanIn(from_chapter=1, to_chapter=10, target_episodes=80)
    assert body.target_episodes == 80
    # 越界被拦(单次规划硬顶 120,与输出预算/解析帽同步)
    with pytest.raises(Exception):
        PlanIn(from_chapter=1, to_chapter=10, target_episodes=121)


def test_episode_cap_follows_target():
    from app.engines.drama.planner import _EPISODE_CAP_MAX, _MAX_EPISODES, _episode_cap

    # 无目标:维持旧的 40 帽(防模型跑飞)
    assert _episode_cap(0) == _MAX_EPISODES
    # 有目标:盖住 ±10% 浮动上限(80 目标 → 模型最多 ~88 集,帽 90)
    assert _episode_cap(80) == 90
    assert _episode_cap(120) == _EPISODE_CAP_MAX
    assert _episode_cap(200) == _EPISODE_CAP_MAX
