# tests/test_plan_flow.py
# -*- coding: utf-8 -*-
"""开书方案流(确认链 L0 新形态,docs/22 P0):三问 → 整书方案×3 → 定向修订 → 拍板。

覆盖:
- POST /three-questions:候选+首推唯一化;第 3 问随模式分叉;题材边界注入
- POST /book-plans:三套方案存工作集(book_plans);answers/avoid 进 prompt;坏 JSON 502
- POST /revise-plan:只改第 index 套;空字段回填原值;越界 400
- POST /plan-confirm:方案渲染成开书订单(brief)+brief_confirmed=True;
  档位映射(方案推荐档/屏 0 手选覆盖);书名落库;拍板后概念深化硬门放行
- mode 落库:PATCH mode 合法值/脏值收敛
"""
from __future__ import annotations

import json

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


def _create_project(client: TestClient, headers: dict, title: str, **extra) -> dict:
    body = {"title": title, "target_chapters": 3}
    body.update(extra)
    r = client.post("/api/projects", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


class _FakeAdapter:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_prompt = ""

    async def ask(self, prompt: str, **kw) -> str:  # noqa: ANN003
        self.last_prompt = prompt
        return self.payload


_QUESTIONS_JSON = json.dumps({
    "questions": [
        {"key": "q1", "title": "写什么味道", "candidates": [
            {"text": "都市异闻·冷峻悬疑", "recommended": True, "reason": "贴作者的题材"},
            {"text": "都市温情·治愈日常", "recommended": False, "reason": ""},
            {"text": "都市黑幕·冷硬写实", "recommended": True, "reason": "重复推荐"},
        ]},
        {"key": "q2", "title": "主角是谁", "candidates": [
            {"text": "口吃档案员女警,过目不忘", "recommended": True, "reason": "反差最强"},
            {"text": "跑单王骑手,市井江湖气", "recommended": False, "reason": ""},
        ]},
        {"key": "q3", "title": "最大的坎是什么", "candidates": [
            {"text": "泄密者就在身边", "recommended": False, "reason": ""},
            {"text": "体制本身是共谋", "recommended": True, "reason": "张力最大"},
        ]},
    ]
}, ensure_ascii=False)

_PLANS_JSON = json.dumps({
    "plans": [
        {"title": "第七份笔录", "kernel": "口吃女警靠背诵旧案笔录串并七起悬案",
         "protagonist": "林小满,27岁,档案室内勤,想要一次被当警察看的机会",
         "world": "当代南方省会,纯刑侦逻辑,没有神探直觉",
         "arc": "开局笔录雷同无人信;私下串并发现师父在场;卷尾签名页被撕",
         "engine": "每解一桩旧案暴露一层关系网", "flavor": ["冷硬写实", "慢热燃"],
         "scale": "长篇", "scale_reason": "七案七层网,值得铺", "label": "小人物·体制·冷硬"},
        {"title": "外卖死亡路线", "kernel": "跑单王骑手查兄弟坠桥真相",
         "protagonist": "周大勇,34岁,想要真相和接女儿的钱",
         "world": "平台算法统治的都市", "arc": "异常订单备注;黑中介据点;女儿被接走",
         "engine": "每一单外卖都是一条线索", "flavor": ["市井硬汉"],
         "scale": "中篇", "scale_reason": "单线复仇节奏快", "label": "骑手·算法·燃"},
        {"title": "失眠者电台", "kernel": "失眠主持人在直播里与预告杀人者声音博弈",
         "protagonist": "苏晚,31岁,想睡一个整觉",
         "world": "被短视频挤压的深夜电台", "arc": "来电预告成真;直播间成秀场;凶手在楼内",
         "engine": "每期节目一场声音猫鼠", "flavor": ["心理惊悚"],
         "scale": "连载", "scale_reason": "单元案可无限续", "label": "主持·声音·惊悚"},
    ]
}, ensure_ascii=False)


def test_three_questions_sanitizes_and_splits_by_mode(client, monkeypatch):
    headers = _auth(client, "plans_3q_user")
    p = _create_project(client, headers, "三问书", topic="想写都市悬疑",
                        global_tendency={"genre": "都市悬疑"})
    from app.api.projects import plans as plans_mod

    adapter = _FakeAdapter(_QUESTIONS_JSON)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: adapter)
    r = client.post(f"/api/projects/{p['id']}/three-questions", headers=headers,
                    json={"mode": "serial", "topic": "想写都市悬疑", "genre": "都市悬疑"})
    assert r.status_code == 200, r.text
    questions = r.json()["questions"]
    assert [q["key"] for q in questions] == ["q1", "q2", "q3"]
    # 首推唯一化:模型标了两个 recommended,只认第一个
    recs = [c for c in questions[0]["candidates"] if c["recommended"]]
    assert len(recs) == 1 and recs[0]["text"].startswith("都市异闻")
    # 空候选被丢弃
    assert len(questions[1]["candidates"]) == 2
    # 连载第 3 问 = 最大的坎;题材边界注入
    assert "最大的坎" in adapter.last_prompt
    assert "严守题材边界" in adapter.last_prompt

    # 短故事模式:第 3 问换成结尾情绪
    r = client.post(f"/api/projects/{p['id']}/three-questions", headers=headers,
                    json={"mode": "short", "topic": "", "genre": ""})
    assert r.status_code == 200
    assert "结尾想落在什么感觉" in adapter.last_prompt
    assert "老套路" in adapter.last_prompt  # 无题材时的自由边界


def test_book_plans_stores_working_set(client, monkeypatch):
    headers = _auth(client, "plans_wall_user")
    p = _create_project(client, headers, "方案书", topic="一句话灵感",
                        global_tendency={"genre": "都市悬疑"})
    from app.api.projects import plans as plans_mod

    adapter = _FakeAdapter(_PLANS_JSON)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: adapter)
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers,
                    json={"mode": "serial", "topic": "一句话灵感", "genre": "都市悬疑",
                          "answers": {"q1": "都市异闻·冷峻悬疑", "q2": "口吃档案员女警",
                                      "q3": "泄密者就在身边"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["plans"]) == 3
    assert data["plans"][2]["scale"] == "连载"
    assert data["project"]["book_plans"] is not None
    assert len(data["project"]["book_plans"]) == 3
    # 三问答案、差异轴要求、方案一直读原则都要进 prompt
    assert "口吃档案员女警" in adapter.last_prompt
    assert "方案一直读作者的想法" in adapter.last_prompt
    # 再来三套:avoid 进 prompt
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers,
                    json={"mode": "serial", "topic": "一句话灵感", "genre": "都市悬疑",
                          "answers": {}, "feedback": "太灰了,来点亮堂的",
                          "avoid": ["小人物·体制·冷硬", "骑手·算法·燃", "主持·声音·惊悚"]})
    assert r.status_code == 200
    assert "太灰了" in adapter.last_prompt
    assert "避开" in adapter.last_prompt
    # 坏 JSON → 502
    monkeypatch.setattr(plans_mod, "get_adapter_for",
                        lambda task, **kw: _FakeAdapter("不是 JSON"))
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers,
                    json={"mode": "serial"})
    assert r.status_code == 502


def test_revise_plan_only_touches_index_and_backfills(client, monkeypatch):
    headers = _auth(client, "plans_revise_user")
    p = _create_project(client, headers, "修订书")
    pid = p["id"]
    from app.api.projects import plans as plans_mod

    seed = _FakeAdapter(_PLANS_JSON)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: seed)
    r = client.post(f"/api/projects/{pid}/book-plans", headers=headers, json={"mode": "serial"})
    assert r.status_code == 200

    # 修订第 0 套:模型只回 protagonist 和 flavor,还把 world 丢了 → 回填原值
    revised = json.dumps({
        "title": "第七份笔录", "kernel": "口吃女警靠背诵旧案笔录串并七起悬案",
        "protagonist": "林小满,28岁,档案室内勤,想要一次被当警察看的机会(改)",
        "world": "", "arc": "", "engine": "", "flavor": ["冷硬写实", "慢热燃", "孤勇"],
    }, ensure_ascii=False)
    rev_adapter = _FakeAdapter(revised)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: rev_adapter)
    r = client.post(f"/api/projects/{pid}/revise-plan", headers=headers,
                    json={"index": 0, "directive": "主角年龄改成28"})
    assert r.status_code == 200, r.text
    plans = r.json()["plans"]
    assert "(改)" in plans[0]["protagonist"]
    assert plans[0]["world"]  # 空值回填,格子不丢
    assert plans[0]["flavor"] == ["冷硬写实", "慢热燃", "孤勇"]
    assert plans[1]["title"] == "外卖死亡路线"  # 其余套不动
    # 修订 prompt 必须带「未要求修改的字段逐字保留」(实验修正)
    assert "逐字保留" in rev_adapter.last_prompt
    assert "主角年龄改成28" in rev_adapter.last_prompt

    # 越界 → 400
    r = client.post(f"/api/projects/{pid}/revise-plan", headers=headers,
                    json={"index": 9, "directive": "x"})
    assert r.status_code == 400
    # 没方案 → 400
    p2 = _create_project(client, headers, "空方案书")
    r = client.post(f"/api/projects/{p2['id']}/revise-plan", headers=headers,
                    json={"index": 0, "directive": "x"})
    assert r.status_code == 400


def test_plan_confirm_renders_order_and_unlocks_concept(client, monkeypatch):
    headers = _auth(client, "plans_confirm_user")
    p = _create_project(client, headers, "未命名新书")
    pid = p["id"]
    from app.api.projects import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_adapter_for",
                        lambda task, **kw: _FakeAdapter(_PLANS_JSON))
    r = client.post(f"/api/projects/{pid}/book-plans", headers=headers, json={"mode": "serial"})
    assert r.status_code == 200

    # 拍板第 0 套(推荐档:长篇 150 章)
    r = client.post(f"/api/projects/{pid}/plan-confirm", headers=headers,
                    json={"index": 0, "mode": "serial"})
    assert r.status_code == 200, r.text
    proj = r.json()
    assert proj["brief_confirmed"] is True
    assert "【故事内核】口吃女警" in proj["brief"]
    assert "【首卷走向】" in proj["brief"]
    assert proj["title"] == "第七份笔录"  # 未命名书 → 落方案书名
    assert proj["target_chapters"] == 150  # 方案推荐档「长篇」
    assert proj["target_words_per_chapter"] == 3000

    # 屏 0 手选档位覆盖方案推荐:第 1 套(中篇)但手选短篇 20 章
    p2 = _create_project(client, headers, "覆盖档位书")
    pid2 = p2["id"]
    r = client.post(f"/api/projects/{pid2}/book-plans", headers=headers, json={"mode": "serial"})
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid2}/plan-confirm", headers=headers,
                    json={"index": 1, "mode": "serial",
                          "scale_override": {"chapters": 20, "words": 3000}})
    proj2 = r.json()
    assert proj2["target_chapters"] == 20
    assert proj2["brief_confirmed"] is True

    # 拍板后概念深化硬门放行(不 409):挂假深化适配器,确认 job 正常起跑
    from app.api import inspire as inspire_mod

    develop = _FakeAdapter(json.dumps({
        "logline": "口吃女警串并七起悬案", "hook": "笔录雷同", "twist": "师父在场",
        "protagonist": "林小满", "conflict": "体制与真相", "setting": "档案室",
        "sell": "笨拙的执拗",
    }, ensure_ascii=False))
    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task, **kw: develop)
    r = client.post(f"/api/projects/{pid}/concept-from-brief-async", headers=headers)
    assert r.status_code == 200, r.text
    assert "job_id" in r.json()


def test_short_mode_plans_and_confirm(client, monkeypatch):
    headers = _auth(client, "plans_short_user")
    p = _create_project(client, headers, "短故事书", mode="short")
    assert p["mode"] == "short"
    from app.api.projects import plans as plans_mod

    short_json = json.dumps({
        "plans": [{
            "title": "最后一课", "kernel": "代课老师用最后一节课送走想辍学的学生",
            "protagonist": "陈默,58岁,想体面地退场",
            "world": "县城中学,冬天", "arc": "开端发现辍学信;转折家访见真相;结尾空座位上放着一封信",
            "ending": "怅然·微光", "flavor": ["温情"], "scale": "8千字",
            "scale_reason": "单一事件", "label": "老师·挽留·温情",
        }, {
            "title": "夜班公交", "kernel": "末班车司机每晚会多等一个不存在的乘客",
            "protagonist": "老周,50岁", "world": "城市深夜公交", "arc": "起末班怪客;转揭亡妻;结空站台的灯",
            "ending": "酸楚·释然", "flavor": ["都市传说"], "scale": "3千字",
            "scale_reason": "一个反转", "label": "司机·执念·传说",
        }],
    }, ensure_ascii=False)
    adapter = _FakeAdapter(short_json)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: adapter)
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers,
                    json={"mode": "short", "topic": "想写个短故事", "answers": {}})
    assert r.status_code == 200, r.text
    assert "短故事" in adapter.last_prompt
    assert "结尾情绪" in adapter.last_prompt

    r = client.post(f"/api/projects/{p['id']}/plan-confirm", headers=headers,
                    json={"index": 0, "mode": "short"})
    proj = r.json()
    assert proj["mode"] == "short"
    assert "【故事弧】" in proj["brief"] and "结尾落在:怅然·微光" in proj["brief"]
    assert proj["target_chapters"] == 1 and proj["target_words_per_chapter"] == 8000


def test_patch_mode_dirty_value_falls_back(client):
    headers = _auth(client, "plans_mode_user")
    p = _create_project(client, headers, "模式书")
    r = client.patch(f"/api/projects/{p['id']}", headers=headers, json={"mode": "short"})
    assert r.json()["mode"] == "short"
    r = client.patch(f"/api/projects/{p['id']}", headers=headers, json={"mode": "宇宙无敌"})
    assert r.json()["mode"] == "serial"  # 脏值收敛


_QUESTIONS_B_JSON = json.dumps({
    "questions": [{"key": "q1", "title": "写什么味道", "candidates": [
        {"text": "东方奇幻·诡谲瑰丽", "recommended": True, "reason": "避开上一批"},
        {"text": "历史权谋·苍凉厚重", "recommended": False, "reason": ""},
    ]}]
}, ensure_ascii=False)


def test_three_questions_injects_avoid(client, monkeypatch):
    """「🎲换一批」防趋同:上一批候选进 prompt 避开清单(温度 0.9 发散档)。"""
    headers = _auth(client, "plans_3q_avoid_user")
    p = _create_project(client, headers, "三问防趋同书")
    from app.api.projects import plans as plans_mod

    adapter = _FakeAdapter(_QUESTIONS_B_JSON)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: adapter)
    r = client.post(f"/api/projects/{p['id']}/three-questions", headers=headers,
                    json={"mode": "serial", "topic": "都市悬疑",
                          "avoid": ["都市异闻·冷峻悬疑", "口吃档案员女警,过目不忘却无人信"]})
    assert r.status_code == 200, r.text
    # 上一批候选必须注入避开清单
    assert "避开清单" in adapter.last_prompt
    assert "都市异闻·冷峻悬疑" in adapter.last_prompt
    assert "同义或换皮" in adapter.last_prompt


def test_book_plans_avoid_includes_kernel_and_hard_rules(client, monkeypatch):
    """再来三套:avoid 升级为 label·title·kernel,并带「严禁换皮」硬约束。"""
    headers = _auth(client, "plans_avoid_kernel_user")
    p = _create_project(client, headers, "方案防趋同书")
    from app.api.projects import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: _FakeAdapter(_PLANS_JSON))
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers, json={"mode": "serial"})
    assert r.status_code == 200

    captured = _FakeAdapter(_PLANS_JSON)
    monkeypatch.setattr(plans_mod, "get_adapter_for", lambda task, **kw: captured)
    # 第二次带 avoid(前端会拼 label·title·kernel 首句)
    r = client.post(f"/api/projects/{p['id']}/book-plans", headers=headers,
                    json={"mode": "serial",
                          "avoid": ["小人物·体制·冷·第七份笔录·口吃女警靠背诵旧案笔录串并七起悬案"]})
    assert r.status_code == 200
    assert "严禁换皮重出" in captured.last_prompt
    assert "口吃女警靠背诵旧案笔录串并七起悬案" in captured.last_prompt
    assert "味道组合不得原样复用" in captured.last_prompt
