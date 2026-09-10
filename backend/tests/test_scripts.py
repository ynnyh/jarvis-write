# tests/test_scripts.py
# -*- coding: utf-8 -*-
"""剧本工坊 API 测试(TestClient + mock LLM,无需 API key)。

验证点:
- CRUD:空标题兜底「未命名剧本」、列表倒序、详情、删除、跨用户 404
- 分集大纲:prompt 带剧名/类型/一句话/集数;旧集清空重建;status → outlined
- 大纲容错:模型没吐出可用 episodes → 502
- 手改集:content 更新字数;title/status 更新;跨剧本集号 404
- 单集生成:prompt 带上一集结尾(截 400 字)、风格备忘、补充方向;落库 status=drafted
- 小说改编:定稿章 → 剧本(source_project_id 回填)+ 分集;无定稿章 400;他人项目 404
"""
from __future__ import annotations

import json
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


class _Adapter:
    """固定回复桩;记录 prompt 供断言。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


class _DispatchAdapter:
    """按 prompt 内容分派回复的桩(大纲 / 剧本正文 / 集末契约各回各的)。"""

    def __init__(self, *, outline: str = "", episode: str = "", end_state: str = "{}"):
        self.outline = outline
        self.episode = episode
        self.end_state = end_state
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        if "on_stage" in prompt:          # 集末交接契约提取
            return self.end_state
        if '"episodes"' in prompt:        # 分集大纲 / 改编
            return self.outline
        return self.episode               # 单集剧本正文


# 够长、带多个场景标题行的剧本正文(长度需过格式门禁的 120 字下限)
_EPISODE_BODY = (
    "第一场 内景·夜·当铺\n"
    "掌柜:(抬眼)当什么?\n客人:当一段往事。\n"
    "柜台上那枚戒指滚了半圈,停在灯影里,没人去捡。\n\n"
    "第二场 外景·夜·长街\n"
    "雨没停。客人把领子竖起来,拐进巷口,身后当铺的灯一盏盏灭下去。\n"
    "更夫的梆子响了两声,像是在数谁还醒着。\n\n"
    "第三场 内景·晨·渡口茶棚\n"
    "堂倌:(擦桌子)客官,这么早?\n客人:等人。\n"
    "他把那张当票摊在桌上,墨迹被水汽洇开了一角。"
)


def _outline_reply(n: int = 4) -> str:
    return json.dumps({"episodes": [
        {
            "episode_number": i,
            "title": f"第{i}集题",
            "synopsis": f"第{i}集梗概,冲突升级。",
            "opening_hook": f"开场钩子{i}",
            "ending_hook": f"结尾钩子{i}",
        }
        for i in range(1, n + 1)
    ]}, ensure_ascii=False)


# ---------- CRUD ----------

def test_script_crud_and_isolation(client):
    headers = _auth(client, "scripts_user")
    other = _auth(client, "scripts_other")

    # 创建:空标题兜底;默认 12 集
    r = client.post("/api/scripts", headers=headers, json={"title": "  "})
    assert r.status_code == 200, r.text
    s1 = r.json()
    assert s1["title"] == "未命名剧本"
    assert s1["target_episodes"] == 12 and s1["status"] == "empty"
    assert s1["source_project_id"] is None

    # 集数边界:1 集非法
    assert client.post("/api/scripts", headers=headers,
                       json={"title": "x", "target_episodes": 1}).status_code == 422

    r = client.post("/api/scripts", headers=headers,
                    json={"title": "长夜灯", "genre": "悬疑", "logline": "一盏灯照出三代人的秘密",
                          "target_episodes": 6})
    s2 = r.json()

    # 列表倒序(新在前);只看到自己的
    rows = client.get("/api/scripts", headers=headers).json()
    assert [row["id"] for row in rows] == [s2["id"], s1["id"]]
    assert client.get("/api/scripts", headers=other).json() == []

    # 详情 / 跨用户 404
    assert client.get(f"/api/scripts/{s2['id']}", headers=headers).json()["title"] == "长夜灯"
    assert client.get(f"/api/scripts/{s2['id']}", headers=other).status_code == 404

    # 改设定:风格备忘/一句话/集数;标题纯空白兜底不专名
    r = client.patch(f"/api/scripts/{s2['id']}", headers=headers,
                     json={"style_memo": "台词短,少形容词", "logline": "灯灭了以后",
                           "target_episodes": 8, "title": "   "})
    assert r.status_code == 200, r.text
    assert r.json()["style_memo"] == "台词短,少形容词"
    assert r.json()["target_episodes"] == 8 and r.json()["title"] == "未命名剧本"

    # 删除 + 复查 404
    assert client.delete(f"/api/scripts/{s1['id']}", headers=headers).json() == {"deleted": True}
    assert client.get(f"/api/scripts/{s1['id']}", headers=headers).status_code == 404


# ---------- 分集大纲 ----------

def test_generate_outline_rebuilds_episodes(client):
    headers = _auth(client, "scripts_outline")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "雾都迷案", "genre": "悬疑", "logline": "法医在雾里认出死去的自己",
        "target_episodes": 4,
    }).json()["id"]

    adapter = _Adapter(_outline_reply(4))
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/scripts/{sid}/generate-outline", headers=headers)
    assert r.status_code == 200, r.text
    eps = r.json()["episodes"]
    assert [e["episode_number"] for e in eps] == [1, 2, 3, 4]
    assert eps[0]["title"] == "第1集题" and eps[0]["status"] == "outlined"

    # prompt 带全要素
    prompt = adapter.prompts[0]
    assert "雾都迷案" in prompt and "悬疑" in prompt and "法医在雾里认出死去的自己" in prompt
    assert "4 集" in prompt

    # 幂等重建:再生成一次,旧的 4 集被清掉,不残留 8 集
    with patch("app.api.scripts.get_adapter_for", return_value=_Adapter(_outline_reply(2))):
        eps2 = client.post(f"/api/scripts/{sid}/generate-outline", headers=headers).json()["episodes"]
    assert [e["episode_number"] for e in eps2] == [1, 2]
    assert client.get(f"/api/scripts/{sid}", headers=headers).json()["status"] == "outlined"


def test_generate_outline_llm_garbage_502(client):
    headers = _auth(client, "scripts_outline_bad")
    sid = client.post("/api/scripts", headers=headers, json={"title": "空转"}).json()["id"]
    with patch("app.api.scripts.get_adapter_for", return_value=_Adapter("抱歉,我做不到")):
        r = client.post(f"/api/scripts/{sid}/generate-outline", headers=headers)
    assert r.status_code == 502
    with patch("app.api.scripts.get_adapter_for",
               return_value=_Adapter(json.dumps({"episodes": []}))):
        assert client.post(f"/api/scripts/{sid}/generate-outline", headers=headers).status_code == 502


# ---------- 集:手改 / 单集生成 ----------

def _script_with_outline(client, headers: dict, sid: int):
    with patch("app.api.scripts.get_adapter_for", return_value=_Adapter(_outline_reply(3))):
        eps = client.post(f"/api/scripts/{sid}/generate-outline", headers=headers).json()["episodes"]
    return eps


def test_patch_episode(client):
    headers = _auth(client, "scripts_patch")
    sid = client.post("/api/scripts", headers=headers, json={"title": "手改本"}).json()["id"]
    eps = _script_with_outline(client, headers, sid)

    r = client.patch(f"/api/scripts/{sid}/episodes/{eps[0]['episode_number']}",
                     headers=headers, json={"content": "第一场:夜,码头。", "status": "drafted"})
    assert r.status_code == 200, r.text
    assert r.json()["word_count"] == len("第一场:夜,码头。")
    assert r.json()["status"] == "drafted"

    # 只改标题不动内容
    r2 = client.patch(f"/api/scripts/{sid}/episodes/{eps[0]['episode_number']}",
                      headers=headers, json={"title": "码头夜"})
    assert r2.json()["title"] == "码头夜" and r2.json()["word_count"] == len("第一场:夜,码头。")

    # 不存在的集号
    assert client.patch(f"/api/scripts/{sid}/episodes/99",
                        headers=headers, json={"title": "x"}).status_code == 404


def test_generate_episode_uses_prev_tail_and_memo(client):
    headers = _auth(client, "scripts_gen")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "长街", "genre": "年代",
        "target_episodes": 3,
    }).json()["id"]
    eps = _script_with_outline(client, headers, sid)

    # 先给第 1 集正文(超 400 字,验证只带结尾 400 字);补风格备忘
    long_text = "开场雨夜。" + "他沿着长街走了很久。" * 80
    client.patch(f"/api/scripts/{sid}/episodes/1", headers=headers, json={"content": long_text})
    from app.db.session import SessionLocal
    from app.db.models import Script
    db = SessionLocal()
    db.query(Script).filter(Script.id == sid).update({"style_memo": "台词短,少形容词"})
    db.commit()
    db.close()

    adapter = _Adapter(_EPISODE_BODY)
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/scripts/{sid}/episodes/2/generate", headers=headers,
                        json={"extra_direction": "本集多写市井声"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "drafted"

    prompt = adapter.prompts[0]
    assert "第 2 集" in prompt and "年代" in prompt
    assert long_text[-400:] in prompt      # 结尾 400 字整段在场
    assert "台词短,少形容词" in prompt      # 风格备忘
    assert "本集多写市井声" in prompt        # 补充方向
    assert eps[1]["synopsis"] in prompt    # 本集梗概

    # 生成失败 → 502 且不落库
    with patch("app.api.scripts.get_adapter_for", return_value=_Adapter("   ")):
        assert client.post(f"/api/scripts/{sid}/episodes/3/generate",
                           headers=headers, json={}).status_code == 502
    eps_now = client.get(f"/api/scripts/{sid}/episodes", headers=headers).json()
    assert next(e for e in eps_now if e["episode_number"] == 3)["content"] == ""


def test_generate_episode_rejects_broken_output(client):
    """格式门禁:输出没有场景标题行 → 整发重试一次;仍崩坏则 502 且不落库。"""
    headers = _auth(client, "scripts_gate")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "断桥", "genre": "悬疑", "target_episodes": 2,
    }).json()["id"]
    _script_with_outline(client, headers, sid)

    # 够长但没有场景标题行 —— 模型跑偏成了散文
    adapter = _Adapter("这一集讲的是一个人在雨夜里反复回想往事,想起的那些人一个个走远了。" * 6)
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/scripts/{sid}/episodes/1/generate", headers=headers, json={})
    assert r.status_code == 502, r.text
    assert "场景标题" in r.json()["detail"]
    assert len(adapter.prompts) >= 2  # 崩坏后确实重试了一次
    eps = client.get(f"/api/scripts/{sid}/episodes", headers=headers).json()
    assert next(e for e in eps if e["episode_number"] == 1)["content"] == ""


def test_end_state_feeds_next_episode(client):
    """集末交接契约:写完后落 extra,下一集 prompt 注入「上一集集末状态」。"""
    headers = _auth(client, "scripts_state")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "渡口", "genre": "年代", "target_episodes": 3,
    }).json()["id"]
    _script_with_outline(client, headers, sid)

    end_state = json.dumps({
        "in_story_time": "第三日 深夜", "location": "破庙内",
        "on_stage": ["沈墨", "老船工"],
        "character_states": [{"name": "沈墨", "state": "左臂刀伤未愈", "doing": "刚入睡"}],
        "resolved": ["戒指当了三两银子"],
        "open_threads": ["庙外脚步声未查明"],
    }, ensure_ascii=False)
    adapter = _DispatchAdapter(episode=_EPISODE_BODY, end_state=end_state)
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        assert client.post(f"/api/scripts/{sid}/episodes/1/generate",
                           headers=headers, json={}).status_code == 200
        r2 = client.post(f"/api/scripts/{sid}/episodes/2/generate", headers=headers, json={})
    assert r2.status_code == 200, r2.text

    from app.db.session import SessionLocal
    from app.db.models import ScriptEpisode

    db = SessionLocal()
    row = db.query(ScriptEpisode).filter(
        ScriptEpisode.script_id == sid, ScriptEpisode.episode_number == 1
    ).first()
    assert row.extra.get("end_state_status") == "ok"
    assert row.extra["end_state"]["location"] == "破庙内"
    db.close()

    # 第 2 集的 prompt 拿到了上一集的集末状态(契约供事实,不再只靠末 400 字)
    assert any("上一集集末状态" in p for p in adapter.prompts)
    assert any("破庙内" in p for p in adapter.prompts)
    assert any("庙外脚步声未查明" in p for p in adapter.prompts)


def test_regenerate_keeps_previous_version(client):
    """重写不丢旧版:覆盖正文前存一版到 extra.versions。"""
    headers = _auth(client, "scripts_ver")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "旧稿", "genre": "剧情", "target_episodes": 2,
    }).json()["id"]
    _script_with_outline(client, headers, sid)
    client.patch(f"/api/scripts/{sid}/episodes/1", headers=headers,
                 json={"content": _EPISODE_BODY})

    adapter = _DispatchAdapter(episode=_EPISODE_BODY + "\n\n第四场 外景·日·码头\n船靠岸了。")
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        assert client.post(f"/api/scripts/{sid}/episodes/1/generate",
                           headers=headers, json={}).status_code == 200

    from app.db.session import SessionLocal
    from app.db.models import ScriptEpisode

    db = SessionLocal()
    row = db.query(ScriptEpisode).filter(
        ScriptEpisode.script_id == sid, ScriptEpisode.episode_number == 1
    ).first()
    versions = list(row.extra.get("versions") or [])
    db.close()
    assert len(versions) == 1 and _EPISODE_BODY in versions[0]["content"]


def test_episode_versions_list_and_restore(client):
    """历史版本可查可回退:覆盖正文前存一版,回退后当前稿也存一版。"""
    headers = _auth(client, "scripts_vrestore")
    sid = client.post("/api/scripts", headers=headers, json={
        "title": "回档", "genre": "剧情", "target_episodes": 2,
    }).json()["id"]
    _script_with_outline(client, headers, sid)

    first = _EPISODE_BODY
    second = _EPISODE_BODY + "\n\n第四场 外景·日·码头\n船靠岸了,没人下船。"
    # 首版(此前是空稿)→ 不存版;再改 → 把首版存成 v1
    assert client.patch(f"/api/scripts/{sid}/episodes/1", headers=headers,
                        json={"content": first}).status_code == 200
    r = client.patch(f"/api/scripts/{sid}/episodes/1", headers=headers,
                     json={"content": second})
    assert r.status_code == 200 and r.json()["content"] == second
    assert r.json()["versions"] == 1
    # 内容没变 → 不重复存版
    assert client.patch(f"/api/scripts/{sid}/episodes/1", headers=headers,
                        json={"content": second}).json()["versions"] == 1

    versions = client.get(f"/api/scripts/{sid}/episodes/1/versions", headers=headers).json()
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["source"] == "manual"
    assert versions[0]["content"] == first and versions[0]["word_count"] == len(first)

    # 回退到 v1 → 正文变回 first,且当前稿(second)被存成新的一版
    r = client.post(f"/api/scripts/{sid}/episodes/1/versions/1/restore", headers=headers)
    assert r.status_code == 200 and r.json()["content"] == first
    versions = client.get(f"/api/scripts/{sid}/episodes/1/versions", headers=headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["source"] == "before-restore-v1" and versions[0]["content"] == second
    # 不存在的版本 → 404
    assert client.post(f"/api/scripts/{sid}/episodes/1/versions/99/restore",
                       headers=headers).status_code == 404


# ---------- 小说改编 ----------

def _make_project_with_final_chapters(username: str, n: int = 3) -> int:
    from app.db.models import Chapter, Project, User
    from app.db.session import SessionLocal

    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    p = Project(title="雾都旧事", genre="悬疑", target_chapters=10, user_id=user.id)
    db.add(p)
    db.commit()
    for i in range(1, n + 1):
        text = f"第{i}章正文。" + "雾漫过码头。" * 30
        db.add(Chapter(project_id=p.id, chapter_number=i,
                       final_content=text, word_count=len(text), status="approved"))
    db.add(Chapter(project_id=p.id, chapter_number=99, final_content="", word_count=0))
    db.commit()
    pid = p.id
    db.close()
    return pid


def test_adapt_to_script_flow(client):
    headers = _auth(client, "scripts_adapt")
    pid = _make_project_with_final_chapters("scripts_adapt", n=2)

    reply = json.dumps({
        "logline": "旧案重启,雾散人非",
        "adapt_note": "合并次要支线,保留法医线",
        "episodes": [
            {"episode_number": i, "title": f"雾{i}",
             "synopsis": f"第{i}集梗概。", "opening_hook": "钩", "ending_hook": "子"}
            for i in range(1, 3)
        ],
    }, ensure_ascii=False)
    adapter = _Adapter(reply)
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/adapt-to-script", headers=headers,
                        json={"target_episodes": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["episodes"] == 2 and body["logline"] == "旧案重启,雾散人非"

    # 剧本落库:回源项目、风格备忘=取舍说明
    s = client.get(f"/api/scripts/{body['script_id']}", headers=headers).json()
    assert s["source_project_id"] == pid and s["title"] == "《雾都旧事》改编"
    eps = client.get(f"/api/scripts/{body['script_id']}/episodes", headers=headers).json()
    assert [e["episode_number"] for e in eps] == [1, 2]

    # prompt 里有定稿正文与原著信息
    assert "雾漫过码头" in adapter.prompts[0] and "雾都旧事" in adapter.prompts[0]

    # 无定稿章的项目 → 400
    pid2 = _make_project_with_final_chapters("scripts_adapt", n=0)
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        r2 = client.post(f"/api/projects/{pid2}/adapt-to-script", headers=headers,
                         json={"target_episodes": 2})
    assert r2.status_code == 400

    # 他人项目 → 404
    other = _auth(client, "scripts_adapt_other")
    with patch("app.api.scripts.get_adapter_for", return_value=adapter):
        assert client.post(f"/api/projects/{pid}/adapt-to-script", headers=other,
                           json={"target_episodes": 2}).status_code == 404
