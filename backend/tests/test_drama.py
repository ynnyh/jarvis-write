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
        self.prompts: list[str] = []  # 收到的提示词(校验注入了什么)

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
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
        {"title": "雪夜杀机", "source_chapters": [1], "hook": "刀光劈开雪幕",
         "recap": "镖队夜宿荒庙遭袭", "cliffhanger": "箱底伸出一只手"},
        # 用旧键 source_chapter 且越界:验证兼容 + 收敛
        {"title": "箱中人", "source_chapter": 99, "hook": "撬开箱盖",
         "recap": "开箱发现大活人", "cliffhanger": "追兵火把照亮山道"},
        {"title": "", "source_chapters": [2], "hook": "无效应被丢弃",
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
    assert eps[1]["source_chapters"] == [1] and eps[1]["source_label"] == "第 1 章"
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
    # 分镜总时长 14s 远短于目标 90s → 如实提示,不装作正常
    assert job["result"]["truncated"] is False
    assert "短于目标" in job["result"]["notice"]

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

    # ---- 5. 单格重出:只动这一格,额外要求进提示词 ----
    single = _JsonAdapter({"shots": [
        # seq 故意写错(999):单格场景下只有一条结果,引擎照用
        {"seq": 999, "prompt_cn": "收刀回鞘,近景,目光扫向镖箱", "prompt_en": "sheath sword, close-up",
         "negative": "低分辨率"},
    ]})
    with patch("app.engines.drama.prompt_render.get_adapter_for", return_value=single):
        r = client.post(f"/api/projects/{pid}/drama/shots/{shots[1]['id']}/prompt",
                        headers=headers, json={"note": "刀锋上要有一道血线"})
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert len(single.prompts) == 1  # 只跑一格,不是整集重跑
    assert "刀锋上要有一道血线" in single.prompts[0]  # 额外要求进了提示词
    one = job["result"]["shot"]
    assert one["seq"] == 2 and "收刀回鞘" in one["prompt_cn"]
    assert "国风厚涂" in one["prompt_cn"] and "玄色劲装" in one["prompt_cn"]  # 锚段兜底照旧
    assert shots[0]["prompt_cn"] in (
        client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
        .json()["shots"][0]["prompt_cn"]
    )  # 别的格没被动
    # 最后一格补齐 → 整集转 ready
    assert job["result"]["episode"]["status"] == "ready"

    # ---- 6. 手动改提示词照旧可用 ----
    r = client.patch(f"/api/projects/{pid}/drama/shots/{shots[1]['id']}", headers=headers,
                     json={"prompt_cn": "收刀回鞘,近景", "prompt_en": "sheath sword, close-up"})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["shots"][1]["prompt_cn"] == "收刀回鞘,近景"


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


def test_multi_chapter_episode(client):
    """数章并一集:源章号存全集,写剧本时逐章取正文(只喂主章会丢内容)。"""
    headers = _auth(client, "drama_multi")
    p = _create_project(client, headers, "并集漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=2)

    with patch("app.engines.drama.planner.get_adapter_for",
               return_value=_JsonAdapter({"episodes": [
                   {"title": "两章并一集", "source_chapters": [2, 1, 99],
                    "hook": "h", "recap": "r", "cliffhanger": "c"},
               ]})):
        r = client.post(f"/api/projects/{pid}/drama/episodes/plan", headers=headers,
                        json={"from_chapter": 1, "to_chapter": 2})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    ep = job["result"][0]
    assert ep["source_chapters"] == [1, 2]  # 升序去重,越界的 99 丢弃
    assert ep["source_chapter"] == 1  # 主源章 = 最小章号
    assert ep["source_label"] == "第 1-2 章"

    adapter = _JsonAdapter(_SCRIPT_REPLY)
    with patch("app.engines.drama.script.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep['id']}/script", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    sent = adapter.prompts[0]
    assert "—— 第 1 章 ——" in sent and "—— 第 2 章 ——" in sent  # 两章正文都进了上下文
    assert "第 1-2 章" in sent


def test_storyboard_cap_scales_with_duration(client):
    """镜头数上限按目标时长动态算;超上限如实报数,不静默截断。"""
    from app.engines.drama.storyboard import shot_cap

    assert shot_cap(30) == 30  # 30s / 每格最短 1s
    assert shot_cap(180) == 120  # 夹在上限内
    assert shot_cap(10) == 10  # 短集也按 1s/格走,下限只在 <8s 时才兜底

    headers = _auth(client, "drama_cap")
    p = _create_project(client, headers, "上限漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)
    _seed_assets(pid)

    with patch("app.engines.drama.planner.get_adapter_for",
               return_value=_JsonAdapter({"episodes": [_PLAN_REPLY["episodes"][0]]})):
        r = client.post(f"/api/projects/{pid}/drama/episodes/plan", headers=headers,
                        json={"from_chapter": 1, "to_chapter": 1, "duration_s": 30})
        job = _wait_job(client, headers, r.json()["job_id"])
    ep_id = job["result"][0]["id"]
    with patch("app.engines.drama.script.get_adapter_for",
               return_value=_JsonAdapter(_SCRIPT_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/script", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])

    over = _JsonAdapter({"shots": [
        {"seq": i, "scene_name": "荒山雪道", "characters": ["沈砚"],
         "action_desc": f"第{i}格画面", "shot_type": "近景", "camera": "固定",
         "dialogue": "", "duration_s": 1}
        for i in range(1, 37)  # 36 格,超 30 格上限(30s / 每格 1s)
    ]})
    with patch("app.engines.drama.storyboard.get_adapter_for", return_value=over):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/storyboard", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert "镜头数上限 30 格" in over.prompts[0]  # 上限进了提示词
    assert len(job["result"]["shots"]) == 30
    assert job["result"]["truncated"] is True
    assert "多给了 6 格" in job["result"]["notice"]
    assert "短于目标" not in job["result"]["notice"]  # 30×1=30s 已达目标


# ---- 阶段 2:声线选型 + 成片包 ----

_VOICE_REPLY = {
    "casts": [
        {"name": "沈砚", "tts_hint": "剪映:沉稳青年男声;火山:青涩青年男声;MiniMax:deep_male",
         "reading_notes": "语速偏慢,压着说,「看人」二字咬重"},
    ]
}

_PACK_REPLY = {
    "shots": [
        {"seq": 1, "transition": "推入", "bgm_tag": "低弦压场", "tts_text": "走镖不看路,看人。"},
        {"seq": 2, "transition": "叠化", "bgm_tag": "低弦压场", "tts_text": ""},
    ]
}


def test_voice_cast_updates_cards(client):
    """声线选型:补 tts_hint/reading_notes;locked 卡跳过。"""
    headers = _auth(client, "drama_voice")
    p = _create_project(client, headers, "声线漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)

    from app.db.session import SessionLocal
    from app.db.models import DramaCharacterCard, Entity

    with SessionLocal() as s:
        ent = s.query(Entity).filter(Entity.project_id == pid, Entity.name == "沈砚").first()
        s.add(DramaCharacterCard(
            project_id=pid, entity_id=ent.id, name="沈砚",
            appearance_cn="三十岁,剑眉", voice_desc="青年男声,低沉克制",
        ))
        s.commit()

    with patch("app.engines.drama.voice.get_adapter_for",
               return_value=_JsonAdapter(_VOICE_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/voice-cast/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cards = job["result"]["cards"]
    assert cards[0]["tts_hint"].startswith("剪映")
    assert "咬重" in cards[0]["reading_notes"]

    # 锁定后重跑:声线字段不再被覆盖
    cid = cards[0]["id"]
    client.patch(f"/api/projects/{pid}/drama/characters/{cid}", headers=headers,
                 json={"locked": True, "tts_hint": "手工版"})
    with patch("app.engines.drama.voice.get_adapter_for",
               return_value=_JsonAdapter(_VOICE_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/voice-cast/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["result"]["skipped_locked"] == 1
    assert job["result"]["cards"][0]["tts_hint"] == "手工版"


def _pipeline_to_storyboard(client, headers, pid: int) -> int:
    """helper:规划→剧本→分镜,返回首集 id(成片包测试的前置)。"""
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
    return ep_id


def test_production_pack_and_exports(client):
    """成片包:配音稿(说话人匹配/声线映射/估时)+ 剪辑清单 + SRT/成片包导出。"""
    headers = _auth(client, "drama_pack")
    p = _create_project(client, headers, "成片包漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)
    _seed_assets(pid)  # 角色卡带 voice_desc/tts_hint 由声线测试覆盖,这里用基础卡
    ep_id = _pipeline_to_storyboard(client, headers, pid)

    # 未生成前导出成片包 → 400
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=pack", headers=headers)
    assert r.status_code == 400

    with patch("app.engines.drama.production.get_adapter_for",
               return_value=_JsonAdapter(_PACK_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/episodes/{ep_id}/pack", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    pack = job["result"]["pack"]
    # 配音稿:镜头 1 有台词,说话人从剧本匹配到「沈砚」,声线来自角色卡
    assert len(pack["dubbing"]) == 1
    d = pack["dubbing"][0]
    assert d["speaker"] == "沈砚"
    assert "低沉克制" in d["voice"]
    assert d["tts_text"] == "走镖不看路,看人。"
    assert d["est_s"] >= 1 and d["shot_duration_s"] == 4
    # 剪辑清单:两格,LLM 标注并入,默认转场兜底
    assert len(pack["checklist"]) == 2
    assert pack["checklist"][0]["transition"] == "推入"
    assert pack["checklist"][1]["transition"] == "叠化"
    assert pack["totals"]["shots"] == 2
    assert pack["totals"]["storyboard_s"] == 14  # 4 + 10
    # 对白模式无整段口播
    assert pack["narration_full"] == ""

    # GET pack 回读
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/pack", headers=headers)
    assert r.status_code == 200 and r.json()["pack"]["totals"]["shots"] == 2

    # SRT:时间轴按分镜累计,只有镜头 1 出字幕条(镜头 2 无台词)
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=srt", headers=headers)
    assert r.status_code == 200
    srt = r.text
    assert "1\n00:00:00,000 --> 00:00:04,000\n走镖不看路,看人。" in srt
    assert "00:00:04,000 --> 00:00:14,000" not in srt  # 无台词镜头不占字幕条

    # 成片包 Markdown:配音稿表 + 剪辑清单表
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=pack", headers=headers)
    assert r.status_code == 200
    md = r.text
    assert "配音稿" in md and "剪辑清单" in md
    assert "走镖不看路,看人。" in md
    assert "低弦压场" in md


# ---- 预告片 ----

_TRAILER_REPLY = {
    "title": "这场镖,押的是命",
    "lines": [
        {"speaker": "旁白", "text": "雪夜,镖队出动"},
        {"speaker": "沈砚", "text": "走镖不看路,看人。"},
    ],
    "shots": [
        {"seq": 1, "source_ep": 1, "scene_name": "荒山雪道", "characters": ["沈砚"],
         "action_desc": "刀光劈开雪幕", "shot_type": "特写", "camera": "推",
         "dialogue": "走镖不看路,看人。", "duration_s": 3,
         "prompt_cn": "刀光劈开雪幕(漏锚段,验证兜底)", "prompt_en": "blade light", "negative": "低分辨率"},
        {"seq": 2, "source_ep": 0, "scene_name": "荒山雪道", "characters": [],
         "action_desc": "标题卡前的定格", "shot_type": "全景", "camera": "固定",
         "dialogue": "", "duration_s": 2, "prompt_cn": "雪道远全景", "prompt_en": "snowy road wide", "negative": ""},
        {"seq": 3, "source_ep": 1, "scene_name": "荒山破庙", "characters": ["沈砚"],
         "action_desc": "箱底伸出一只手", "shot_type": "特写", "camera": "推",
         "dialogue": "", "duration_s": 2, "prompt_cn": "箱中伸手的特写,画风锚已含国风厚涂", "prompt_en": "hand from crate close-up, ink-wash wuxia, cinematic, dark teal palette", "negative": ""},
        {"seq": 4, "source_ep": 1, "scene_name": "荒山雪道", "characters": ["沈砚"],
         "action_desc": "沈砚回眸", "shot_type": "近景", "camera": "固定",
         "dialogue": "", "duration_s": 99, "prompt_cn": "回眸近景,含国风厚涂", "prompt_en": "look back, ink-wash wuxia, cinematic, dark teal palette", "negative": ""},
    ],
}


def test_trailer_generate_and_export(client):
    """预告片:生成(锚段兜底)+ GET 回读 + 导出(md/srt);无集时 400。"""
    headers = _auth(client, "drama_trailer")
    p = _create_project(client, headers, "预告片漫剧书")
    pid = p["id"]
    _seed_novel(pid, chapters=1)
    _seed_assets(pid)

    # 还没切集 → 生成失败(job error,业务引导)
    r = client.post(f"/api/projects/{pid}/drama/trailer/generate", headers=headers,
                    json={"from_ep": 1, "to_ep": 9, "target_s": 30})
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error"
    assert "切集" in job["error"]

    ep_id = _pipeline_to_storyboard(client, headers, pid)

    with patch("app.engines.drama.trailer.get_adapter_for",
               return_value=_JsonAdapter(_TRAILER_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/trailer/generate", headers=headers,
                        json={"from_ep": 1, "to_ep": 9, "target_s": 30})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    trailer = job["result"]
    assert trailer["title"] == "这场镖,押的是命"
    assert len(trailer["shots"]) == 4
    # 镜头 1 桩故意漏画风锚 → 兜底注入;角色锚同样兜底
    assert "国风厚涂" in trailer["shots"][0]["prompt_cn"]
    assert "玄色劲装" in trailer["shots"][0]["prompt_cn"]
    # 镜头 1 英文漏锚 → 兜底
    assert "ink-wash" in trailer["shots"][0]["prompt_en"]
    # 镜头 4 时长 99 → 预告片上限 8
    assert trailer["shots"][3]["duration_s"] == 8
    # 负面词基座并入
    assert trailer["shots"][1]["negative"].startswith("文字,水印")

    # GET 回读
    r = client.get(f"/api/projects/{pid}/drama/trailer", headers=headers)
    assert r.status_code == 200
    assert r.json()["trailer"]["totals"]["shots"] == 4

    # 导出:md 含文案骨架与提示词;srt 时间轴累计(3+2+2+8)
    r = client.get(f"/api/projects/{pid}/drama/trailer/export?format=md", headers=headers)
    assert r.status_code == 200
    assert "文案骨架" in r.text and "混剪分镜" in r.text
    assert "这场镖,押的是命" in r.text
    r = client.get(f"/api/projects/{pid}/drama/trailer/export?format=srt", headers=headers)
    assert r.status_code == 200
    assert "1\n00:00:00,000 --> 00:00:03,000\n走镖不看路,看人。" in r.text




# ---- 画风方向 + 方向推荐 ----

_STYLE_REPLY = {
    "style_name": "水墨武侠·电影感",
    "style_cn": "国风水墨,墨色浓淡相宜,留白构图,侧逆光",
    "style_en": "ink-wash wuxia, cinematic",
    "negative": "文字,水印",
}

_DIRREC_REPLY = {
    "recommendations": [
        {"key": "ink_wash", "reason": "武侠+雪夜荒山,水墨最贴"},
        {"key": "comic_cn", "reason": "备选"},
        {"key": "nonsense_key", "reason": "应被丢弃"},
        {"key": "auto", "reason": "auto 不应入选"},
        {"key": "cyber", "reason": "第三名"},
    ]
}


def test_style_direction_and_recommend(client):
    """方向:生成时持久化 + 非法方向 400;推荐:归一化(未知 key/auto 丢弃、限 3、带优先级)。"""
    headers = _auth(client, "drama_dir")
    p = _create_project(client, headers, "方向漫剧书")
    pid = p["id"]
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"topic": "雪夜镖队护送神秘镖箱", "genre": "武侠"})
    assert r.status_code == 200

    # meta 带方向目录
    r = client.get(f"/api/projects/{pid}/drama/meta", headers=headers)
    dirs = r.json()["directions"]
    assert any(d["key"] == "live" and "恐怖谷" in d["tip"] for d in dirs)

    # 非法方向 → 400
    r = client.post(f"/api/projects/{pid}/drama/style/generate", headers=headers,
                    json={"direction": "pixel_art"})
    assert r.status_code == 400 and "方向" in r.json()["detail"]
    r = client.put(f"/api/projects/{pid}/drama/style", headers=headers,
                   json={"style_cn": "x", "direction": "pixel_art"})
    assert r.status_code == 400

    # 指定方向生成 → 卡上持久化,序列化带 label
    with patch("app.engines.drama.style.get_adapter_for",
               return_value=_JsonAdapter(_STYLE_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/style/generate", headers=headers,
                        json={"direction": "ink_wash"})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["direction"] == "ink_wash"
    assert job["result"]["direction_label"] == "水墨国风"

    # 方向推荐:未知 key 与 auto 被丢,按序号限 3,带优先级
    with patch("app.engines.drama.style.get_adapter_for",
               return_value=_JsonAdapter(_DIRREC_REPLY)):
        r = client.post(f"/api/projects/{pid}/drama/style/recommend-directions", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    recs = job["result"]["recommendations"]
    assert [x["key"] for x in recs] == ["ink_wash", "comic_cn", "cyber"]
    assert [x["priority"] for x in recs] == [1, 2, 3]
    assert recs[0]["label"] == "水墨国风"
