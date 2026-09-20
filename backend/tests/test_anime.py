# tests/test_anime.py
# -*- coding: utf-8 -*-
"""动画短剧全链路测试:系列 CRUD / 卡司(生成+锁定保留+手改归一) / 梗纲三选一 /
分镜 / 整集分段提示词 / 归属隔离。LLM 全程打桩(按提示词关键词路由假响应)。
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


def _uid(client: TestClient, username: str) -> int:
    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        return s.query(User).filter(User.username == username).first().id


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


class _FakeAdapter:
    """按提示词关键词路由假响应:卡司/梗纲/聊天/分镜回 JSON,整集提示词回纯文本。"""

    def __init__(self, film_reply: str = ""):
        self.prompts: list[str] = []
        self.film_reply = film_reply

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        if "动画角色设计总监" in prompt:
            return json.dumps({"cast": _CAST_JSON}, ensure_ascii=False)
        if "动画编剧搭档" in prompt:
            return json.dumps({
                "reply": "接住了:豆包偷吃主菜,我补了阿丸用蛋救场的反转。",
                "synopsis": _CHAT_SYNOPSIS,
            }, ensure_ascii=False)
        if "动画编剧总监" in prompt:
            return json.dumps({"takes": _TAKES_JSON}, ensure_ascii=False)
        if "动画的分镜师" in prompt:
            return json.dumps(_SHOTS_JSON, ensure_ascii=False)
        return f"```text\n{self.film_reply}\n```"


_CHAT_SYNOPSIS = (
    "年夜饭夜,阿丸一本正经立下军令状要独力撑起全场年夜饭;豆包趁乱偷吃,主菜肉眼可见"
    "变小;阿丸发现后不拆穿,反手煎出一颗比锅还大的蛋压住全场;收尾全场举杯,豆包一个"
    "嗝把自己崩出画面,只剩红斗篷挂在椅背上——落点:那件空斗篷的定格。"
)


_CAST_JSON = [
    {"name": "阿丸", "role": "主角", "appearance": "一只圆滚滚的白色饭团精灵,体态像发好的馒头,"
     "脸上有两团淡淡的红晕,左脸颊一颗芝麻大小的痣(跨集认脸的记忆点)", "wardrobe": "蓝色围裙"
     "(棉布,深蓝滚边),脖子上挂一把小银勺", "personality": "认真到较真,慌张时原地转圈",
     "catchphrase": "包在我身上!"},
    {"name": "豆包", "role": "配角", "appearance": "黄色小黄豆,只有阿丸半个头高,圆身体短手脚,"
     "眼睛占脸一半", "wardrobe": "红色小斗篷", "personality": "天不怕地不怕,闯祸王",
     "catchphrase": "看我的!"},
    {"name": "锅盖", "role": "配角", "appearance": "灰色铁锅盖成精,扁平圆身,边缘一个缺口,"
     "走路咣当作响", "wardrobe": "无(本体即服装)", "personality": "慢半拍的老好人",
     "catchphrase": ""},
]

_TAKES_JSON = [
    {"logline": "阿丸接下全场年夜饭的重任,豆包偷吃把主菜越吃越小,阿丸用一颗蛋救回全场",
     "beats": ["拍1:阿丸一本正经立下军令状", "拍2:豆包偷吃,主菜肉眼可见变小",
               "拍3:阿丸灵机一动把蛋煎成巨无霸", "拍4:全场干杯,豆包打嗝飞出去"],
     "punchline": "豆包打嗝飞出画面,只剩斗篷挂在椅背上", "highlight": "误会连锁+物理惩罚"},
    {"logline": "锅盖想帮忙擦桌子却越擦越脏,三人比赛擦桌子,最后发现脏的是抹布",
     "beats": ["拍1:锅盖自告奋勇", "拍2:越擦越脏,三人加码比赛",
               "拍3:阿丸发现抹布才是元凶", "拍4:锅盖把自己擦得锃亮邀功"],
     "punchline": "锅盖锃亮的反光晃到镜头,黑屏一秒", "highlight": "重复升级+预期违背"},
    {"logline": "停电夜三人讲鬼故事,豆包被自己讲的鬼吓晕,『鬼』原来是锅盖的缺口影子",
     "beats": ["拍1:烛光里开讲", "拍2:豆包越讲越怕", "拍3:墙上有『鬼影』逼近",
               "拍4:开灯,是锅盖缺口投的影子"],
     "punchline": "豆包晕在前,锅盖慢半拍说『影子是我』", "highlight": "悬念误导+身份错位"},
]

_SHOTS_JSON = {
    "title": "年夜饭保卫战",
    "shots": [
        {"seq": i, "shot_type": "中景" if i % 2 else "特写",
         "camera": "固定" if i % 3 else "缓推", "duration_s": 5,
         "action_desc": f"第{i}镜:阿丸在灶台前颠勺,围裙上沾着面粉,动作干净利落",
         "dialogue": "包在我身上!" if i == 1 else ("看我的!" if i == 6 else ""),
         "speaker": "阿丸" if i == 1 else ("豆包" if i == 6 else ""),
         "characters": (["阿丸"] if i % 2 else ["阿丸", "豆包"]),
         "sfx": "锅铲刮底声" if i == 3 else ""}
        for i in range(1, 13)  # 12 镜 × 5s = 60s
    ],
}

_FILM_REPLY = (
    "【第1段|0—15秒】Q版二头身漫画风,线条圆润上色干净。厨房全景,暖黄灯光。"
    "阿丸(白色饭团精灵,蓝色围裙)郑重系紧围裙带子,镜头从围裙特写拉开到全景,"
    "他举起小银勺指向天空:『包在我身上!』——音色亮、语速快、气势十足。"
    "锅铲落锅的一声脆响压在动作落点上。衔接:硬切。\n"
    "【第2段|15—30秒】同厨房。豆包(黄色小黄豆,红色斗篷)踮脚偷吃,主菜肉眼可见变小。"
    "衔接:延续:本段末帧作下段首帧(图生视频)。\n"
    "【第3段|30—45秒】阿丸额头冒汗,煎蛋下锅,油花四溅成一朵金色烟花。"
    "镜头急推到蛋的特写再拉回全景,蛋比锅还大。衔接:硬切。\n"
    "【第4段|45—60秒】全场举杯,豆包一个嗝把自己崩出画面,只剩红斗篷挂在椅背上,"
    "定格。全片结束"
)


@pytest.fixture()
def anime_user(client):
    return _auth(client, f"anime_u_{int(time.time() * 1000) % 10 ** 9}")


def _mk_series(client, headers, **kw) -> int:
    body = {"title": "饭团小厨房", "premise": "饭团精灵阿丸的厨房日常",
            "genre": "comedy", "direction": "chibi", "episode_s": 60}
    body.update(kw)
    r = client.post("/api/anime", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["series"]["id"]


# =============== 目录 / 系列 CRUD ===============


def test_anime_meta(client):
    headers = _auth(client, "anime_meta")
    r = client.get("/api/anime/meta", headers=headers)
    assert r.status_code == 200
    m = r.json()
    keys = [g["key"] for g in m["genres"]]
    assert "comedy" in keys and len(keys) >= 5
    assert all(d["key"] != "auto" for d in m["directions"])
    assert m["episode_s"] == [60, 90]


def test_anime_create_validations(client):
    headers = _auth(client, "anime_valid")
    r = client.post("/api/anime", headers=headers, json={"genre": "宫斗"})
    assert r.status_code == 400
    r = client.post("/api/anime", headers=headers, json={"direction": "auto"})
    assert r.status_code == 400
    r = client.post("/api/anime", headers=headers, json={"episode_s": 45})
    assert r.status_code == 400


def test_anime_series_crud_and_ownership(client):
    headers = _auth(client, "anime_crud")
    sid = _mk_series(client, headers)
    got = client.get(f"/api/anime/{sid}", headers=headers)
    assert got.status_code == 200
    s = got.json()["series"]
    assert s["genre"] == "comedy" and s["status"] == "cast_empty"
    assert "Q版" in s["style_cn"]  # 画风锚默认取方向硬约束

    r = client.patch(f"/api/anime/{sid}", headers=headers,
                     json={"title": "饭团小厨房·第二季", "episode_s": 90})
    assert r.status_code == 200 and r.json()["series"]["episode_s"] == 90

    other = _auth(client, "anime_crud_other")
    assert client.get(f"/api/anime/{sid}", headers=other).status_code == 404
    assert client.delete(f"/api/anime/{sid}", headers=other).status_code == 404
    assert client.delete(f"/api/anime/{sid}", headers=headers).status_code == 200


# =============== 卡司 ===============


def test_anime_cast_generate_and_locked_keep(client):
    headers = _auth(client, "anime_cast")
    sid = _mk_series(client, headers)

    adapter = _FakeAdapter(_FILM_REPLY)
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/{sid}/cast", headers=headers)
        assert r.status_code == 200
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cast = client.get(f"/api/anime/{sid}", headers=headers).json()["series"]["cast"]
    assert len(cast) == 3
    heroes = [c for c in cast if c["role"] == "主角"]
    assert len(heroes) == 1 and heroes[0]["name"] == "阿丸"

    # 锁定阿丸后重出:阿丸原样保留,新提案只补不锁的位子
    cast[0]["locked"] = True
    r = client.put(f"/api/anime/{sid}/cast", headers=headers, json={"cast": cast})
    assert r.status_code == 200
    adapter2 = _FakeAdapter(_FILM_REPLY)
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter2):
        r = client.post(f"/api/anime/{sid}/cast", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cast2 = client.get(f"/api/anime/{sid}", headers=headers).json()["series"]["cast"]
    names = [c["name"] for c in cast2]
    assert "阿丸" in names and cast2[[c["name"] for c in cast2].index("阿丸")]["appearance"] \
        .startswith("一只圆滚滚")

    # 手改保存归一:两个主角会被压成一个
    dup = [dict(c) for c in cast2]
    dup[1]["role"] = "主角"
    r = client.put(f"/api/anime/{sid}/cast", headers=headers, json={"cast": dup})
    got = r.json()["series"]["cast"]
    assert len([c for c in got if c["role"] == "主角"]) == 1


def test_anime_episode_requires_cast(client):
    headers = _auth(client, "anime_ep")
    sid = _mk_series(client, headers)
    r = client.post(f"/api/anime/{sid}/episodes", headers=headers, json={"premise": "值日"})
    assert r.status_code == 400 and "卡司" in r.json()["detail"]


# =============== 对话式简介:聊天 → 确认 → 分镜门控 ===============


def _cast_ready_series(client, headers, adapter) -> int:
    sid = _mk_series(client, headers)
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/{sid}/cast", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    return sid


def _episode_of(client, headers, sid) -> dict:
    eps = client.get(f"/api/anime/{sid}", headers=headers).json()["episodes"]
    assert eps, "至少要有一集"
    return eps[0]


def test_anime_chat_confirm_gate(client):
    """点子聊天出简介草稿 → 未确认时分镜锁死 → 确认解锁 → 再聊天重新上锁。"""
    headers = _auth(client, "anime_chat")
    adapter = _FakeAdapter(_FILM_REPLY)
    sid = _cast_ready_series(client, headers, adapter)
    r = client.post(f"/api/anime/{sid}/episodes", headers=headers, json={"premise": "年夜饭"})
    eid = r.json()["episode"]["id"]

    # 第一轮聊天:AI 补充完善出简介草稿
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/chat", headers=headers,
                        json={"message": "豆包偷吃年夜饭主菜,阿丸用一颗蛋救场"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "简介" in body["reply"] or body["reply"]
    assert body["synopsis"].startswith("年夜饭夜")
    ep = _episode_of(client, headers, sid)
    assert len(ep["chat"]) == 2 and ep["chat"][0]["role"] == "user"
    assert ep["synopsis_ok"] is False and ep["synopsis"].startswith("年夜饭夜")
    assert ep["status"] == "premise"  # 草稿不是拍板

    # 未确认:分镜锁死
    r = client.post(f"/api/anime/episodes/{eid}/shots", headers=headers)
    assert r.status_code == 400 and "确认简介" in r.json()["detail"]

    # 确认 → 解锁
    r = client.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=headers)
    assert r.status_code == 200
    ep = r.json()["episode"]
    assert ep["synopsis_ok"] is True and ep["status"] == "synopsis_ready"

    # 再聊一轮:新草稿作废旧确认,分镜重新上锁
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/chat", headers=headers,
                        json={"message": "结尾改成锅盖背锅"})
    assert r.status_code == 200
    ep = _episode_of(client, headers, sid)
    assert len(ep["chat"]) == 4 and ep["synopsis_ok"] is False
    r = client.post(f"/api/anime/episodes/{eid}/shots", headers=headers)
    assert r.status_code == 400

    # 重新确认(带手改文本一并替换)→ 解锁;分镜提示词吃的是确认后的简介
    r = client.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=headers,
                    json={"synopsis": "手改后的最终版简介。"})
    assert r.status_code == 200 and r.json()["episode"]["synopsis"] == "手改后的最终版简介。"
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/shots", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    shots_prompt = adapter.prompts[-1]
    assert "手改后的最终版简介" in shots_prompt  # 分镜吃的是确认简介
    assert "用户已确认" in shots_prompt


# =============== 梗纲 → 分镜 → 整集提示词 ===============


def _prepared_episode(client, headers) -> tuple[int, int, _FakeAdapter]:
    """建系列 → 出卡司 → 建集 → 出梗纲 → 选定 → 出分镜,返回 (sid, eid, 最后一个假适配器)。"""
    sid = _mk_series(client, headers)
    adapter = _FakeAdapter(_FILM_REPLY)
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/{sid}/cast", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    r = client.post(f"/api/anime/{sid}/episodes", headers=headers,
                    json={"premise": "阿丸第一次掌勺年夜饭"})
    assert r.status_code == 200, r.text
    eid = r.json()["episode"]["id"]

    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/takes", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    ep = client.get(f"/api/anime/{sid}", headers=headers).json()["episodes"][0]
    assert len(ep["takes"]) == 3

    # 未选定就出分镜:400
    r = client.post(f"/api/anime/episodes/{eid}/shots", headers=headers)
    assert r.status_code == 400

    r = client.post(f"/api/anime/episodes/{eid}/pick", headers=headers, json={"index": 0})
    assert r.status_code == 200
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/shots", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    return sid, eid, adapter


def test_anime_takes_pick_shots_chain(client):
    headers = _auth(client, "anime_chain")
    sid, eid, adapter = _prepared_episode(client, headers)
    ep = client.get(f"/api/anime/{sid}", headers=headers).json()["episodes"][0]
    assert ep["status"] == "shots_ready" and ep["title"] == "年夜饭保卫战"
    total = sum(s["duration_s"] for s in ep["shots"])
    assert abs(total - 60) <= 8  # 分镜时长归一后仍贴目标档
    # 分镜提示词注入了卡司档案与类型节奏
    shots_prompt = adapter.prompts[-1]
    assert "阿丸" in shots_prompt and "蓝色围裙" in shots_prompt
    assert "不许新增有名有姓的角色" in shots_prompt

    # 换梗纲要清下游:再 pick 一次,shots/film_prompt 作废
    r = client.post(f"/api/anime/episodes/{eid}/pick", headers=headers, json={"index": 1})
    ep2 = r.json()["episode"]
    assert ep2["chosen"] == 1 and ep2["shots"] == [] and ep2["film_prompt"] == ""


def test_anime_film_prompt_segmented(client):
    headers = _auth(client, "anime_fp")
    sid, eid, _adapter = _prepared_episode(client, headers)

    adapter = _FakeAdapter(_FILM_REPLY)
    with patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/anime/episodes/{eid}/film-prompt", headers=headers,
                        json={"segment_s": 15})
        assert r.status_code == 200
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job

    got = client.get(f"/api/anime/episodes/{eid}/film-prompt", headers=headers)
    doc = got.json()["film_prompt"]
    assert doc.startswith("【使用说明】")  # 分段文档头(引擎写,复用漫剧同一份)
    assert "【第1段|0—15秒】" in doc and "【第4段" in doc  # 60s → 4 段
    assert "全片结束" in doc
    # 精度口径进原料:画风锚 + 卡司定妆逐字
    prompt = adapter.prompts[-1]
    assert "Q版" in prompt and "芝麻大小的痣" in prompt
    assert "五件事缺一不可" in prompt  # 精度铁律进模板
    # 手改保存整段替换
    r = client.put(f"/api/anime/episodes/{eid}/film-prompt", headers=headers,
                   json={"film_prompt": " 自己写的整集提示词。\n"})
    assert r.status_code == 200
    assert client.get(f"/api/anime/episodes/{eid}/film-prompt", headers=headers) \
        .json()["film_prompt"] == "自己写的整集提示词。"

    other = _auth(client, "anime_fp_other")
    assert client.get(f"/api/anime/episodes/{eid}/film-prompt", headers=other).status_code == 404


def test_anime_episode_delete_conflict_and_ok(client):
    headers = _auth(client, "anime_del")
    sid, eid, _adapter = _prepared_episode(client, headers)
    r = client.delete(f"/api/anime/episodes/{eid}", headers=headers)
    assert r.status_code == 200 and r.json()["ok"] is True
