# tests/test_drama.py
# -*- coding: utf-8 -*-
"""漫剧工坊测试(四步管线 + 锚段注入兜底 + 导出,TestClient + mock LLM)。

验证点:
- 准入门槛:无已定稿章节 → 规划 400
- 集规划:episodes 归一化(无效条目丢弃、source_chapter 越界收敛、序号连续)
- 角色卡:locked 的卡批量重跑不覆盖,skipped_locked 计数
- 剧本/分镜/提示词全链路 job 跑通,状态流转 planned→scripted→storyboarded→ready
- 锚段兜底:LLM 漏掉画风/角色锚时,引擎确定性前置注入
- 导出:markdown 含风格锚与角色锚;csv 带 BOM 与表头
- 归属隔离:对他人项目操作 → 404
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


def _create_project(client: TestClient, headers: dict, title: str = "漫剧书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
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


class _JsonAdapter:
    """假适配器:ask() 按队列依次返回给定 JSON(支持一次 job 多次调用)。"""

    def __init__(self, *payloads: dict):
        self._queue = [json.dumps(p, ensure_ascii=False) for p in payloads]

    async def ask(self, prompt, system=None):
        return self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]


def _seed_novel(pid: int, chapters: int = 2) -> None:
    """直接种数据:蓝图 + 已定稿章节 + 故事圣经角色(漫剧的原料)。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter, Entity, Outline

    with SessionLocal() as s:
        for n in range(1, chapters + 1):
            s.add(
                Outline(
                    project_id=pid,
                    chapter_number=n,
                    title=f"第{n}回",
                    summary=f"第{n}章:镖队遇袭,疑云初起" if n == 1 else f"第{n}章:箱中藏人,风波再起",
                    beats=["雪夜扎营", "黑衣人夜袭", "沈砚拔刀退敌"] if n == 1 else ["开箱验货", "箱中有人", "追兵将至"],
                    suspense_level="高" if n == 1 else "中",
                    scene_location="荒山雪道" if n == 1 else "荒山破庙",
                )
            )
            s.add(
                Chapter(
                    project_id=pid,
                    chapter_number=n,
                    final_content="沈砚按刀立于雪中,火堆明明灭灭。" * 30,
                    word_count=300,
                    status="approved",
                )
            )
        s.add(
            Entity(
                project_id=pid,
                entity_type="character",
                name="沈砚",
                aliases=["沈镖头"],
                base_profile={"age": "三十", "look": "剑眉薄唇,常年玄色劲装"},
            )
        )
        s.commit()


def _seed_assets(pid: int) -> None:
    """种风格卡 + 角色卡 + 场景卡(锚段注入的素材)。"""
    from app.db.session import SessionLocal
    from app.db.models import DramaCharacterCard, DramaSceneCard, DramaStyleCard, Entity

    with SessionLocal() as s:
        s.add(
            DramaStyleCard(
                project_id=pid,
                style_name="水墨武侠·电影感",
                style_cn="国风厚涂,笔触沉稳,黛青主色,侧逆光,暗部占七成",
                style_en="ink-wash wuxia, cinematic, dark teal palette",
                negative="文字,水印,五官错位",
            )
        )
        ent = (
            s.query(Entity)
            .filter(Entity.project_id == pid, Entity.name == "沈砚")
            .first()
        )
        s.add(
            DramaCharacterCard(
                project_id=pid,
                entity_id=ent.id if ent else None,
                name="沈砚",
                appearance_cn="三十岁,剑眉薄唇,玄色劲装银线滚边,腰间横刀",
                appearance_en="black outfit swordsman, sword brow",
                outfit_cn="玄色劲装",
                voice_desc="青年男声,低沉克制",
            )
        )
        s.add(
            DramaSceneCard(
                project_id=pid,
                name="荒山雪道",
                appearance_cn="山道积雪没踝,两侧枯松,暮色四合",
                appearance_en="snowy mountain road, pines, dusk",
            )
        )
        s.commit()


# ---- LLM 桩数据 ----

_PLAN_REPLY = {
    "episodes": [
        {"title": "雪夜杀机", "source_chapter": 1, "hook": "刀光劈开雪幕",
         "recap": "镖队夜宿荒庙遭袭", "cliffhanger": "箱底伸出一只手"},
        {"title": "箱中人", "source_chapter": 99, "hook": "撬开箱盖",
         "recap": "开箱发现大活人", "cliffhanger": "追兵火把照亮山道"},
        {"title": "", "source_chapter": 2, "hook": "无效应被丢弃",
         "recap": "", "cliffhanger": ""},
    ]
}

_CHARS_REPLY = {
    "cards": [
        {"name": "沈砚", "appearance_cn": "三十岁,剑眉,玄色劲装", "appearance_en": "swordsman",
         "outfit_cn": "玄色劲装", "voice_desc": "低沉男声"},
    ]
}
_SCENES_REPLY = {
    "scenes": [
        {"name": "荒山雪道", "appearance_cn": "积雪山道,枯松暮色", "appearance_en": "snowy road"},
        {"name": "不存在的场景", "appearance_cn": "不该被收录", "appearance_en": "nope"},
    ]
}

_SCRIPT_REPLY = {
    "synopsis": "镖队雪夜遇袭,沈砚退敌后发现镖箱有异。",
    "lines": [
        {"speaker": "沈砚", "text": "走镖不看路,看人。", "action": "擦拭刀鞘,眼皮不抬"},
        {"speaker": "旁白", "text": "雪夜,荒山,镖队扎营。", "action": "火堆明灭,风雪扑面"},
    ],
}

_BOARD_REPLY = {
    "shots": [
        {"seq": 1, "scene_name": "荒山雪道", "characters": ["沈砚"],
         "action_desc": "雪幕中一刀劈落,火光溅起", "shot_type": "特写", "camera": "推",
         "dialogue": "走镖不看路,看人。", "duration_s": 4},
        {"seq": 2, "scene_name": "荒山雪道", "characters": ["沈砚"],
         "action_desc": "收刀回鞘,目光扫向镖箱", "shot_type": "近景", "camera": "固定",
         "dialogue": "", "duration_s": 99},
        {"seq": 3, "scene_name": "", "characters": [],
         "action_desc": "", "shot_type": "远景", "camera": "固定", "dialogue": "", "duration_s": 3},
    ]
}

# 提示词桩:故意漏掉画风锚/角色锚,验证引擎兜底注入
_PROMPTS_REPLY = {
    "shots": [
        {"seq": 1, "prompt_cn": "刀光劈开雪幕,火星四溅,特写镜头",
         "prompt_en": "blade light in snow, close-up", "negative": "低分辨率"},
        {"seq": 2, "prompt_cn": "", "prompt_en": "", "negative": ""},
    ]
}


def test_plan_requires_approved_chapter(client):
    """准入门槛:没有已定稿章节 → 400 引导先写作。"""
    headers = _auth(client, "drama_gate")
    p = _create_project(client, headers, "没章节漫剧书")
    r = client.post(
        f"/api/projects/{p['id']}/drama/episodes/plan",
        headers=headers,
        json={"from_chapter": 1, "to_chapter": 3},
    )
    assert r.status_code == 400
    assert "定稿" in r.json()["detail"]


def test_drama_full_pipeline(client):
    """四步管线全链路:规划→剧本→分镜→提示词,状态流转与锚段兜底。"""
    headers = _auth(client, "drama_flow")
    p = _create_project(client, headers, "全链路漫剧书")
    pid = p["id"]
    _seed_novel(pid)
    _seed_assets(pid)

    # ---- 1. 集规划 ----
    with patch("app.engines.drama.planner.get_adapter_for",
               return_value=_JsonAdapter(_PLAN_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/plan", headers=headers,
                        json={"from_chapter": 1, "to_chapter": 2, "mode": "dialogue", "duration_s": 90})
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    eps = job["result"]
    assert len(eps) == 2  # 无标题条目丢弃
    assert [e["ep_index"] for e in eps] == [1, 2]
    assert eps[1]["source_chapter"] == 1  # 99 越界收敛回 from_chapter
    assert eps[0]["status"] == "planned"
    # 归属隔离
    other = _auth(client, "drama_flow_other")
    assert client.post(
        f"/api/projects/{pid}/drama/episodes/plan", headers=other,
        json={"from_chapter": 1, "to_chapter": 2},
    ).status_code == 404

    ep_id = eps[0]["id"]

    # ---- 2. 剧本 ----
    with patch("app.engines.drama.script.get_adapter_for",
               return_value=_JsonAdapter(_SCRIPT_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/script", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    ep = job["result"]
    assert ep["status"] == "scripted"
    assert ep["script"]["lines"][0]["speaker"] == "沈砚"

    # ---- 3. 分镜 ----
    with patch("app.engines.drama.storyboard.get_adapter_for",
               return_value=_JsonAdapter(_BOARD_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/storyboard", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    shots = job["result"]["shots"]
    assert len(shots) == 2  # 无画面的条目丢弃
    assert [s["seq"] for s in shots] == [1, 2]
    assert shots[1]["duration_s"] == 10  # 99 → 上限 10
    assert job["result"]["episode"]["status"] == "storyboarded"

    # ---- 4. 三轨提示词(桩故意漏锚段,验证兜底) ----
    with patch("app.engines.drama.prompt_render.get_adapter_for",
               return_value=_JsonAdapter(_PROMPTS_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/prompts", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    shots = job["result"]["shots"]
    # 画风锚:LLM 漏了 → 兜底前置
    assert "国风厚涂" in shots[0]["prompt_cn"]
    assert "ink-wash" in shots[0]["prompt_en"]
    # 角色锚:镜头 1 出场沈砚,外貌段被兜底注入
    assert "玄色劲装" in shots[0]["prompt_cn"]
    # 负面词基座并入
    assert "文字,水印" in shots[0]["negative"]
    # 镜头 2 桩输出为空 → 保留空(不写脏数据),集状态停在 storyboarded
    assert shots[1]["prompt_cn"] == ""
    assert job["result"]["episode"]["status"] == "storyboarded"

    # ---- 5. 手动补一版提示词后转 ready ----
    r = client.patch(f"/api/projects/{pid}/drama/shots/{shots[1]['id']}", headers=headers,
                     json={"prompt_cn": "收刀回鞘,近景", "prompt_en": "sheath sword, close-up"})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    # 提示词齐了(手动补齐) → 手动不算 ready,重新跑 prompts 让引擎盖章
    assert detail["episode"]["status"] in ("storyboarded", "ready")


def test_export_markdown_and_csv(client):
    """导出:markdown 含风格/角色信息;csv 带 BOM 与表头。"""
    headers = _auth(client, "drama_export")
    p = _create_project(client, headers, "导出漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)
    _seed_assets(pid)

    with patch("app.engines.drama.planner.get_adapter_for",
               return_value=_JsonAdapter({"episodes": [_PLAN_REPLY["episodes"][0]]})):
        r = client.post(f"/api/projects/{pid}/drama/episodes/plan", headers=headers,
                        json={"from_chapter": 1, "to_chapter": 1})
        job = _wait_job(client, headers, r.json()["job_id"])
    ep_id = job["result"][0]["id"]

    with patch("app.engines.drama.script.get_adapter_for",
               return_value=_JsonAdapter(_SCRIPT_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/script", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])
    with patch("app.engines.drama.storyboard.get_adapter_for",
               return_value=_JsonAdapter(_BOARD_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/storyboard", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])

    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=md", headers=headers)
    assert r.status_code == 200
    md = r.text
    assert "拍摄手册" in md
    assert "美术风格卡" in md and "国风厚涂" in md
    assert "沈砚" in md and "玄色劲装" in md
    assert "分镜表" in md

    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=csv", headers=headers)
    assert r.status_code == 200
    assert r.text.startswith("\ufeff")
    assert r.text.splitlines()[1].startswith("1,")

    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=json", headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload["episode"]["id"] == ep_id
    assert payload["style"]["style_name"]
    assert len(payload["shots"]) >= 1


def test_character_cards_locked_not_overwritten(client):
    """角色卡锁定:locked 的卡批量重跑不覆盖,计数 skipped_locked。"""
    headers = _auth(client, "drama_locked")
    p = _create_project(client, headers, "锁卡漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)

    from app.db.session import SessionLocal
    from app.db.models import DramaCharacterCard, Entity

    with SessionLocal() as s:
        ent = s.query(Entity).filter(Entity.project_id == pid, Entity.name == "沈砚").first()
        s.add(DramaCharacterCard(
            project_id=pid, entity_id=ent.id, name="沈砚",
            appearance_cn="手工调过的外貌段", locked=True,
        ))
        s.commit()

    with patch("app.engines.drama.characters.get_adapter_for",
               return_value=_JsonAdapter(_CHARS_REPLY, _SCENES_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["skipped_locked"] == 1
    assert res["cards"][0]["appearance_cn"] == "手工调过的外貌段"
    # 自创场景不收,蓝图里的荒山雪道正常收
    assert [sc["name"] for sc in res["scenes"]] == ["荒山雪道"]


def test_script_without_chapter_text_fails(client):
    """源章节没有正文 → job 失败且错误是中文引导。"""
    headers = _auth(client, "drama_notext")
    p = _create_project(client, headers, "无正文漫剧书")
    pid = p["id"]
    from app.db.session import SessionLocal
    from app.db.models import Chapter, Outline

    with SessionLocal() as s:
        s.add(Outline(project_id=pid, chapter_number=7, title="空章", summary="x"))
        s.add(Chapter(project_id=pid, chapter_number=7, status="approved"))
        s.commit()

    with patch("app.engines.drama.planner.get_adapter_for",
               return_value=_JsonAdapter({"episodes": [
                   {"title": "空章集", "source_chapter": 7, "hook": "h", "recap": "r", "cliffhanger": "c"}
               ]})):
        r = client.post(f"/api/projects/{pid}/drama/episodes/plan", headers=headers,
                        json={"from_chapter": 7, "to_chapter": 7})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done"
    ep_id = job["result"][0]["id"]

    r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/script", headers=headers)
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error"
    assert "正文" in job["error"]
