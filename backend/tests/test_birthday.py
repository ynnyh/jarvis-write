# tests/test_birthday.py
# -*- coding: utf-8 -*-
"""生日祝福工坊测试(两段式批产/三选一/回忆点核对/切段/导出,TestClient + mock LLM)。

验证点:
- CRUD 与归属隔离;缺称呼/缺回忆点/非法基调/关系/时长 → 400
- 两段式批产:①一发定风格+三条切入,②每条切入各一发展开(断言调用次数)
- 寿星资料注入:称呼/回忆点/基调节奏契约进两段提示词正文(模型必须看得见)
- 回忆点核对(生日版红线):分镜没落实的回忆进 cautions;落实了的干净
- 三选一/单条重拍/手卡编辑(重算切段+候选同步)/导出 md·srt
- 出片工作台:懒建盘/整卡回填/参考图上传·读·删·外链/权限隔离/段号对不上 404
- 生成中删除 409;引擎分层门禁里注册 birthday 线(见 test_engine_conventions)
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


class _PhaseAdapter:
    """两段式桩:按提示词特征分流——①要 takes,②要 shots。

    引擎每发都会 get_adapter_for 拿新实例(并发下不能共用),计数落在类属性上,
    便于断言「①一发 + ②三发」。
    """

    calls = {"takes": 0, "expand": 0}

    def __init__(self, head: dict, expand: dict | list, fail_takes: set[str] | None = None):
        self._head = head
        self._expand = expand
        self._fail = fail_takes or set()

    @classmethod
    def reset(cls):
        cls.calls = {"takes": 0, "expand": 0}

    async def ask(self, prompt, system=None):
        if '"takes"' in prompt:
            type(self).calls["takes"] += 1
            return json.dumps(self._head, ensure_ascii=False)
        type(self).calls["expand"] += 1
        for name in self._fail:
            if f"切入:{name}" in prompt:
                raise RuntimeError(f"模拟 {name} 展开失败")
        return json.dumps(self._expand, ensure_ascii=False)


class _CaptureAdapter(_PhaseAdapter):
    """在两段式桩上记录每次收到的提示词(断言注入用)。"""
    prompts: list[str] = []

    @classmethod
    def reset(cls):
        super().reset()
        cls.prompts = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return await super().ask(prompt, system)


def _take(name: str) -> dict:
    """①的一条切入(不含分镜)。"""
    return {
        "take": name,
        "logline": f"{name}的一支 30 秒生日片",
        "emotion_curve": "憋笑→回忆杀→烛光里破防",
        "punchline": "老王,生日快乐,这次换我们罩你。",
        "hook_text": "全员假装忘了老王生日",
    }


def _expand(prompt_cn: str, with_memory: bool = True) -> dict:
    """②的展开结果(台词 + 分镜三轨)。

    with_memory:回忆杀格是否带上给定回忆点的素材(流星雨/天台)——
    关掉它就能造出「AI 没落实回忆点」的脏候选,验证核对红线。
    4 格 7+8+7+8=30s:切段恰并成两段各 15s,总时长无偏差警示。
    """
    memory_scene = "天台流星雨" if with_memory else "陌生的餐厅包间"
    memory_action = "毯子里看流星划过" if with_memory else "碰杯,灯光转暗"
    return {
        "lines": [
            {"speaker": "旁白", "text": "老王,还记得那年的天台吗。", "action": "旧照片缓缓推近"},
        ],
        "shots": [
            {"seq": 1, "scene_name": "办公室", "characters": ["老王"],
             "character_desc": "老王:男,50岁,微胖,灰色polo衫,笑起来眼角堆纹",
             "action_desc": "老王对着一桌子没动的文件发呆", "shot_type": "中景", "camera": "固定",
             "dialogue": "", "duration_s": 7,
             "prompt_cn": prompt_cn, "prompt_en": "office, man at desk", "negative": "低分辨率"},
            {"seq": 2, "scene_name": memory_scene, "characters": ["老王", "同事"],
             "character_desc": "老王:男,50岁,微胖,灰色polo衫;同事:女,28岁,白衬衫",
             "action_desc": memory_action, "shot_type": "全景", "camera": "摇",
             "dialogue": "老王,还记得那年的天台吗。", "duration_s": 8,
             "prompt_cn": f"{memory_scene}全景,{memory_action}", "prompt_en": "rooftop meteor night", "negative": ""},
            {"seq": 3, "scene_name": "走廊", "characters": ["同事们"],
             "character_desc": "同事们:三三两两,捧着蛋糕",
             "action_desc": "众人捧蛋糕蹑手蹑脚下楼", "shot_type": "全景", "camera": "跟随",
             "dialogue": "", "duration_s": 7,
             "prompt_cn": "走廊跟拍,众人捧蛋糕蹑手蹑脚", "prompt_en": "hallway follow shot with cake", "negative": ""},
            {"seq": 4, "scene_name": "办公室", "characters": ["老王", "同事们"],
             "character_desc": "老王:男,50岁,微胖,灰色polo衫;同事们:捧着点亮的蛋糕",
             "action_desc": "灯亮,老王愣住,烛光映在脸侧", "shot_type": "特写", "camera": "推",
             "dialogue": "", "duration_s": 8,
             "prompt_cn": "烛光特写,老王眼眶发热定格", "prompt_en": "candlelight closeup, emotional", "negative": ""},
        ],
    }


_STYLE = {
    "style_name": "胶片温情·暖黄",
    "style_cn": "胶片质感,暖黄主色,自然光,微颗粒",
    "style_en": "warm film look, golden tones, soft grain",
    "negative": "文字,水印",
}

_HEAD = {
    **_STYLE,
    "takes": [_take("假装忘记的生日"), _take("一段天台旧录像"), _take("全公司的秘密任务")],
}

_MEMORIES = ["大学时一起在天台看流星雨", "总把「稳住」当口头禅"]
_EXPAND = _expand("办公室中景(故意漏画风锚)")

_BODY = {
    "honoree_name": "老王",
    "relationship": "friend",
    "milestone": "50岁",
    "memories": _MEMORIES,
    "sender_desc": "全部门同事",
    "tone": "surprise",
    "duration_s": 30,
    "direction": "live",
    "style_hints": "办公室日常、烛光",
}


def _mk_wish(client, headers, extra=None) -> int:
    body = dict(_BODY)
    body.update(extra or {})
    r = client.post("/api/birthday", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["wish_row"]["id"]


def test_birthday_generic_flow(client):
    """通用流:创建校验 → 批产(归一化/兜底/切段/回忆点核对) → 三选一 → 导出。"""
    headers = _auth(client, "bday_user")
    other = _auth(client, "bday_other")

    # 校验:缺称呼/缺回忆点/缺基调/非法关系/非法时长/非法方向 → 400
    bad = _BODY.copy(); bad["honoree_name"] = " "
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["memories"] = []
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["tone"] = ""; bad["custom_tone"] = ""
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["relationship"] = "boss"
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["duration_s"] = 15
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["direction"] = "pixel"
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400

    wid = _mk_wish(client, headers)
    # 归属隔离
    assert client.get(f"/api/birthday/{wid}", headers=other).status_code == 404

    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    candidates = job["result"]["candidates"]
    assert len(candidates) == 3
    # 两段式:①一发定风格与切入,②每条切入各一发
    assert _PhaseAdapter.calls == {"takes": 1, "expand": 3}
    c0 = candidates[0]
    # 30s → 镜头上限 7(四格全保留);7+8+7+8=30 切成两段各 15s
    assert len(c0["shots"]) == 4
    assert [ch["duration_s"] for ch in c0["chunks"]] == [15, 15]
    # 镜头1 故意漏画风锚 → 引擎兜底注入;负面基座并入
    assert "胶片质感" in c0["shots"][0]["prompt_cn"]
    assert "warm film look" in c0["shots"][0]["prompt_en"]
    assert c0["shots"][1]["negative"].startswith("文字,水印")
    # 回忆点「天台看流星雨」已被镜头2 落实;「稳住口头禅」没落实 → 进 cautions
    assert any("稳住" in c and "没在分镜里找到对应画面" in c for c in c0["cautions"])
    assert not any("流星雨" in c for c in c0["cautions"])
    # 角色定妆卡从分镜聚合(编辑保存后仍在,见 save 用例)
    assert any(c["name"] == "老王" for c in c0["character_cards"])

    # 三选一
    r = client.post(f"/api/birthday/{wid}/pick", headers=headers, json={"index": 1})
    assert r.status_code == 200
    row = r.json()["wish_row"]
    assert row["chosen"] == 1 and row["status"] == "picked"
    assert row["clip"]["take"] == "一段天台旧录像"
    assert client.post(f"/api/birthday/{wid}/pick", headers=headers,
                       json={"index": 9}).status_code == 422

    # 导出:md 含寿星资料卡/回忆点清单/出片玩法指引;srt 与分镜同轴
    r = client.get(f"/api/birthday/{wid}/export?format=md", headers=headers)
    assert r.status_code == 200
    assert "生日祝福手卡" in r.text and "寿星资料卡" in r.text
    assert "天台看流星雨" in r.text and "出片玩法指引" in r.text and "生成切段" in r.text
    r = client.get(f"/api/birthday/{wid}/export?format=srt", headers=headers)
    assert "1\n00:00:07,000 --> 00:00:15,000\n老王,还记得那年的天台吗。" in r.text
    r = client.get(f"/api/birthday/{wid}/export?format=json", headers=headers)
    assert r.json()["honoree_name"] == "老王"


def test_pack_injection_and_validation(client):
    """风格包:非法 key 400;选包后世界包 directive 进两段提示词、通用画风方向
    不再注入(两套硬约束会打架);主角植入与场景词模型必须看得见。"""
    headers = _auth(client, "bday_pack")
    bad = _BODY.copy(); bad["pack"] = "pokemon"
    assert client.post("/api/birthday", headers=headers, json=bad).status_code == 400

    wid = _mk_wish(client, headers, extra={"pack": "hero", "direction": "watercolor"})
    row = client.get(f"/api/birthday/{wid}", headers=headers).json()["wish_row"]
    assert row["pack"] == "hero"
    assert "奥特曼同款气质" in row["pack_label"]

    _CaptureAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    takes_prompt = _CaptureAdapter.prompts[0]
    # 世界包三要素:强画风锚 + 世界观场景词 + 主角植入
    assert "风格包" in takes_prompt and "特摄英雄剧" in takes_prompt
    assert "变身闪光" in takes_prompt and "并肩对战" in takes_prompt
    assert "每一格都有TA" in takes_prompt
    # 选了风格包:通用画风方向(水彩)不注入,避免两套硬约束打架
    assert "手绘水彩绘本风" not in takes_prompt
    assert "手绘水彩绘本风" not in _CaptureAdapter.prompts[1]
    # 不选包的建单走通用方向(互斥的另一态)
    wid2 = _mk_wish(client, headers)
    _CaptureAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid2}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert "风格包" not in _CaptureAdapter.prompts[0]
    assert "画风方向" in _CaptureAdapter.prompts[0]

    # patch 可换包/退包
    r = client.patch(f"/api/birthday/{wid}", headers=headers, json={"pack": ""})
    assert r.status_code == 200 and r.json()["wish_row"]["pack"] == ""
    assert client.patch(f"/api/birthday/{wid}", headers=headers,
                        json={"pack": "nope"}).status_code == 400


def test_profile_injection_into_prompts(client):
    """寿星资料是定制感的全部来源:称呼/回忆点/基调节奏契约/氛围关键词必须进
    两段提示词正文(模型看不见就不算注入)。"""
    headers = _auth(client, "bday_inject")
    wid = _mk_wish(client, headers)

    _CaptureAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    takes_prompt = _CaptureAdapter.prompts[0]
    assert "老王" in takes_prompt
    assert "天台看流星雨" in takes_prompt
    assert "惊喜反转" in takes_prompt          # 基调节奏契约
    assert "假装全世界都忘了" in takes_prompt  # 目录 directive 原文
    assert "办公室日常、烛光" in takes_prompt   # 氛围关键词
    assert "结构铁律" in takes_prompt and "三幕节奏契约" in takes_prompt
    assert "俗套黑名单" in takes_prompt
    assert "回忆点红线" in takes_prompt
    # ②的展开提示词同一份 context + 生日特有细节标准
    assert "老王" in _CaptureAdapter.prompts[1]
    assert "保持参考照片" in _CaptureAdapter.prompts[1]


def test_memories_grounding_clean_when_fulfilled(client):
    """分镜把两条回忆点都落实 → cautions 里没有任何回忆警示(只有时长偏差类才算数)。"""
    headers = _auth(client, "bday_ground")
    wid = _mk_wish(client, headers, extra={
        "memories": ["大学时一起在天台看流星雨", "他总在天台喊稳住"],
    })
    expand = _expand("办公室中景,含胶片质感,暖黄主色")
    expand["shots"][1]["dialogue"] = "老王,还记得那年天台喊稳住吗。"
    expand["shots"][1]["action_desc"] = "毯子里看流星,老王喊出稳住"
    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, expand)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    for c in job["result"]["candidates"]:
        assert not any("没在分镜里找到对应画面" in x for x in c["cautions"])


def test_one_take_failure_still_delivers_the_rest(client):
    """②三发并行:一发展开失败(带重试),另外两个本子照样交付。"""
    headers = _auth(client, "bday_partial")
    wid = _mk_wish(client, headers)

    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND, fail_takes={"全公司的秘密任务"})):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cands = job["result"]["candidates"]
    assert [c["take"] for c in cands] == ["假装忘记的生日", "一段天台旧录像"]
    assert _PhaseAdapter.calls == {"takes": 1, "expand": 4}

    # 只剩 1 个就没得「三选一」,按失败上报
    wid2 = _mk_wish(client, headers)
    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND,
                                          fail_takes={"一段天台旧录像", "全公司的秘密任务"})):
        r = client.post(f"/api/birthday/{wid2}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "过少" in job["error"]


def test_style_card_is_shared_by_all_candidates(client):
    """三个本子共用①定下的那一套画风锚(三选一后提示词口径必须一致)。"""
    headers = _auth(client, "bday_style")
    wid = _mk_wish(client, headers)

    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    row = client.get(f"/api/birthday/{wid}", headers=headers).json()["wish_row"]
    assert row["style_cn"] == _STYLE["style_cn"]
    for c in job["result"]["candidates"]:
        assert _STYLE["style_cn"] in c["shots"][0]["prompt_cn"]


def test_patch_profile_roundtrip(client):
    """建后改资料:称呼/回忆点回显;改成空被 400 拦住(定制感的下限)。"""
    headers = _auth(client, "bday_patch")
    wid = _mk_wish(client, headers)
    r = client.patch(f"/api/birthday/{wid}", headers=headers, json={
        "honoree_name": "王工",
        "memories": ["团建时在草原唱跑调的歌", "总把「稳住」当口头禅", "工位上养了盆多肉"],
        "milestone": "45岁",
    })
    assert r.status_code == 200, r.text
    row = r.json()["wish_row"]
    assert row["honoree_name"] == "王工"
    assert row["memories"][0] == "团建时在草原唱跑调的歌"
    assert len(row["memories"]) == 3
    # 称呼/回忆点不许改成空
    assert client.patch(f"/api/birthday/{wid}", headers=headers,
                        json={"honoree_name": " "}).status_code == 400
    assert client.patch(f"/api/birthday/{wid}", headers=headers,
                        json={"memories": []}).status_code == 400
    assert client.patch(f"/api/birthday/{wid}", headers=headers,
                        json={"duration_s": 45}).status_code == 400


def test_generate_feedback_and_reexpand(client):
    """换一批带意见进①提示词;单条重拍只换目标条,重拍已选条则重置三选一。"""
    headers = _auth(client, "bday_reexp")
    wid = _mk_wish(client, headers)

    _CaptureAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
        base = len(_CaptureAdapter.prompts)
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers,
                        json={"feedback": "整蛊力度不够,再狠一点"})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    second_takes = _CaptureAdapter.prompts[base]
    assert "用户意见" in second_takes and "整蛊力度不够" in second_takes
    assert "上一批" in second_takes and "假装忘记的生日" in second_takes

    row = client.get(f"/api/birthday/{wid}", headers=headers).json()["wish_row"]
    before0 = row["candidates"][0]
    assert client.post(f"/api/birthday/{wid}/pick", headers=headers, json={"index": 1}).status_code == 200

    _CaptureAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _expand("重拍后的分镜锚"))):
        r = client.post(f"/api/birthday/{wid}/reexpand", headers=headers,
                        json={"index": 1, "feedback": "台词砍半"})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert any("用户对本条的意见" in p and "台词砍半" in p for p in _CaptureAdapter.prompts)
    assert _CaptureAdapter.calls["takes"] == 0

    row = client.get(f"/api/birthday/{wid}", headers=headers).json()["wish_row"]
    assert "重拍后的分镜锚" in row["candidates"][1]["shots"][0]["prompt_cn"]
    assert row["candidates"][0]["shots"][0]["prompt_cn"] == before0["shots"][0]["prompt_cn"]
    assert row["chosen"] == -1 and row["status"] == "generated"


def test_save_wish_card_recalc_and_sync(client):
    """手卡编辑保存:归一化入库、切段重算、候选同步、回忆点核对仍生效;未选定时拒绝。"""
    headers = _auth(client, "bday_save")
    wid = _mk_wish(client, headers)
    assert client.put(f"/api/birthday/{wid}/card", headers=headers,
                      json={"card": {"shots": []}}).status_code == 400

    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert client.post(f"/api/birthday/{wid}/pick", headers=headers, json={"index": 0}).status_code == 200

    card = client.get(f"/api/birthday/{wid}", headers=headers).json()["wish_row"]["clip"]
    card["punchline"] = "改过的金句。"
    # 砍到两格 7+8=15:切段变一段,「天台流星雨」没了 → 回忆警示出现
    card["shots"] = [card["shots"][0], card["shots"][3]]
    card["lines"] = [{"speaker": "旁白", "text": "改过的台词", "action": ""}]
    r = client.put(f"/api/birthday/{wid}/card", headers=headers, json={"card": card})
    assert r.status_code == 200, r.text
    row = r.json()["wish_row"]
    saved = row["clip"]
    assert saved["punchline"] == "改过的金句。"
    assert len(saved["shots"]) == 2
    assert len(saved["chunks"]) == 1 and saved["chunks"][0]["end_s"] == 15
    assert any("流星雨" in c and "没在分镜里找到对应画面" in c for c in saved["cautions"])
    # 候选与手卡保持同步;定妆卡从编辑后的分镜重建
    assert row["candidates"][0]["punchline"] == "改过的金句。"
    assert any(c["name"] == "老王" for c in saved["character_cards"])


def test_delete_rejected_while_generating(client):
    """生成中拒绝删除:任务收尾 UPDATE 这一行,行没了会 StaleDataError(与 clips 同教训)。"""
    from app import jobs as jobs_mod

    headers = _auth(client, "bday_delguard")
    wid = _mk_wish(client, headers)

    kind = f"birthday-gen-{wid}"
    jid = f"fakeb{wid:04d}"
    with jobs_mod._LOCK:
        jobs_mod._JOBS[jid] = {
            "kind": kind, "status": "running", "owner_id": None,
            "stage": "生成中", "result": None, "error": None,
        }
    try:
        r = client.delete(f"/api/birthday/{wid}", headers=headers)
        assert r.status_code == 409
        assert "正在生成" in r.json()["detail"]
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(jid, None)
        assert client.delete(f"/api/birthday/{wid}", headers=headers).status_code == 200
    finally:
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(jid, None)


# ---------- 出片工作台(按段的参考图/状态/成品) ----------

# 1×1 透明 PNG(只按文件头判定类型,sniff 不过度解码)
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
    b"\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r"
    b"\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _picked_wish(client, headers) -> int:
    """建一条祝福片并走完批产 + 三选一,返回 id(手卡含切段,工作台才有盘可建)。"""
    wid = _mk_wish(client, headers)
    _PhaseAdapter.reset()
    with patch("app.engines.birthday.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/birthday/{wid}/generate", headers=headers)
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert client.post(f"/api/birthday/{wid}/pick", headers=headers, json={"index": 0}).status_code == 200
    return wid


def test_shoot_roundtrip_and_image_ops(client):
    """出片工作台:懒建盘 → 回填状态/成品 → 参考图上传/读/删/贴外链/超限拦。"""
    headers = _auth(client, "bday_shoot")
    wid = _picked_wish(client, headers)

    # 先读第一次:按手卡切段懒建盘(4 格 7+8+7+8 → 两段各 15s)
    r = client.get(f"/api/birthday/{wid}/shoot", headers=headers)
    assert r.status_code == 200, r.text
    shoot = r.json()["shoot"]
    assert len(shoot) == 2
    assert shoot[0]["done"] is False and shoot[0]["result_link"] == ""

    idx = shoot[0]["index"]
    shoot[0]["done"] = True
    shoot[0]["result_link"] = "https://example.com/video/done"
    shoot[0]["note"] = "寿星照片走图生视频,脸没漂"
    r = client.put(f"/api/birthday/{wid}/shoot", headers=headers, json={"shoot": shoot})
    assert r.status_code == 200, r.text
    saved = r.json()["shoot"][0]
    assert saved["done"] is True and saved["result_link"].startswith("https://example.com/video/")
    assert saved["index"] == idx and "subtitle" in saved and saved["shot_seqs"]

    # 上传寿星照片当参考图 → 读回 → 删除(文件连删)
    r = client.post(f"/api/birthday/{wid}/shoot/{idx}/reference", headers=headers,
                    files={"file": ("ref.png", _PNG, "image/png")},
                    data={"note": "寿星年轻时的照片"})
    assert r.status_code == 200, r.text
    shoot = r.json()["shoot"]
    assert len(shoot[0]["ref_images"]) == 1
    assert shoot[0]["ref_images"][0]["kind"] == "upload"
    assert shoot[0]["ref_images"][0]["note"] == "寿星年轻时的照片"

    r = client.get(f"/api/birthday/{wid}/shoot/{idx}/reference/0", headers=headers)
    assert r.status_code == 200, f"READ FAILED: {r.text}"
    assert r.content == _PNG
    assert r.headers["content-type"].startswith("image/png")

    r = client.post(f"/api/birthday/{wid}/shoot/{idx}/reference/link", headers=headers,
                    json={"url": "https://img.example.com/pic.jpg", "note": "外链版"})
    assert r.status_code == 200, r.text
    refs = r.json()["shoot"][0]["ref_images"]
    assert len(refs) == 2 and refs[1]["kind"] == "url"
    assert client.post(f"/api/birthday/{wid}/shoot/{idx}/reference/link", headers=headers,
                       json={"url": "不是链接"}).status_code == 400

    r = client.delete(f"/api/birthday/{wid}/shoot/{idx}/reference/0", headers=headers)
    assert r.status_code == 200 and len(r.json()["shoot"][0]["ref_images"]) == 1
    assert client.get(f"/api/birthday/{wid}/shoot/{idx}/reference/0", headers=headers).status_code == 404

    # 超限防护:3 张上限
    for _ in range(2):
        r = client.post(f"/api/birthday/{wid}/shoot/{idx}/reference/link", headers=headers,
                        json={"url": "https://img.example.com/" + str(_) + ".jpg"})
        assert r.status_code == 200
    r = client.post(f"/api/birthday/{wid}/shoot/{idx}/reference/link", headers=headers,
                    json={"url": "https://img.example.com/x.jpg"})
    assert r.status_code == 400 and "最多 3 张" in r.json()["detail"]


def test_shoot_permission_isolation_and_cleanup(client):
    """出片工作台按用户隔离;段号对不上 404;删单连带清工作台行与上传目录。"""
    headers = _auth(client, "bday_shoot_a")
    other = _auth(client, "bday_shoot_b")
    wid = _picked_wish(client, headers)
    idx = client.get(f"/api/birthday/{wid}/shoot", headers=headers).json()["shoot"][0]["index"]

    assert client.get(f"/api/birthday/{wid}/shoot", headers=other).status_code == 404
    r = client.post(f"/api/birthday/{wid}/shoot/{idx}/reference", headers=other,
                    files={"file": ("a.png", _PNG, "image/png")})
    assert r.status_code == 404

    assert client.post(f"/api/birthday/{wid}/shoot/99/reference", headers=headers,
                       files={"file": ("a.png", _PNG, "image/png")}).status_code == 404

    # 删除祝福片:工作台行被级联带走,参考图目录清掉
    r = client.delete(f"/api/birthday/{wid}", headers=headers)
    assert r.status_code == 200
    from app.db.models import BirthdayShoot
    from app.db.session import SessionLocal
    with SessionLocal() as s:
        gone = s.query(BirthdayShoot).filter(BirthdayShoot.wish_id == wid).first()
        assert gone is None
    from app import storage
    d = storage.upload_root() / "birthday" / str(wid)
    assert not d.exists() or not any(d.iterdir())
