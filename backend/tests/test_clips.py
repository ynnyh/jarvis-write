# tests/test_clips.py
# -*- coding: utf-8 -*-
"""情绪短片工坊测试(两段式批产/三选一/锚段兜底/切段/金句溯源/导出,TestClient + mock LLM)。

验证点:
- CRUD 与归属隔离;非法主题/时长/方向 → 400
- 两段式批产:①一发定风格+三条切入,②每条切入各一发展开(断言调用次数,防退回单次大调用)
- 归一化:镜头上限/时长收敛/切段分组、画风锚兜底、三个本子共用同一套画风
- 容错:一条切入展开失败仍交付其余本子(带重试);少于 2 个则按失败上报
- 三选一:pick 后 clip 落定、status=picked;无效序号 400
- 小说衍生:无定稿章节 → 引导错误;金句 quote_source 不在正文节选 → cautions 标注(溯源红线)
- 导出:md 手卡含金句/切段;srt 时间轴与分镜同轴
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

    引擎每发都会 get_adapter_for 拿新实例(并发下不能共用,预算会互相篡改),
    所以计数落在类属性上,便于断言「①一发 + ②三发」。
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
        # 指定切入展开必失败:验证「一发废了另外两发照样交付」
        for name in self._fail:
            if f"切入:{name}" in prompt:
                raise RuntimeError(f"模拟 {name} 展开失败")
        return json.dumps(self._expand, ensure_ascii=False)


def _take(name: str, quote: str = "") -> dict:
    """①的一条切入(不含分镜)。"""
    return {
        "take": name,
        "logline": f"{name}的一支 15 秒短片",
        "emotion_curve": "平静→屏息→空",
        "punchline": "没说出口的,才最难消化。",
        "hook_text": "他读了二十年她的遗书",
        "quote_source": quote,
    }


def _expand(prompt_cn: str) -> dict:
    """②的展开结果(台词 + 分镜三轨)。"""
    return {
        "lines": [
            {"speaker": "旁白", "text": "有些话,隔了十年才说。", "action": "空教室,粉笔灰浮动"},
        ],
        "shots": [
            {"seq": 1, "scene_name": "空教室", "characters": [],
             "action_desc": "空教室,阳光切出粉笔灰", "shot_type": "全景", "camera": "固定",
             "dialogue": "有些话,隔了十年才说。", "duration_s": 8,
             "prompt_cn": prompt_cn, "prompt_en": "empty classroom, dust in sunbeam", "negative": "低分辨率"},
            {"seq": 2, "scene_name": "空教室", "characters": [],
             "action_desc": "黑板上的字被擦到一半,定格", "shot_type": "特写", "camera": "推",
             "dialogue": "", "duration_s": 7,
             "prompt_cn": "黑板半擦的字特写,含水墨颗粒质感", "prompt_en": "half-erased blackboard closeup", "negative": ""},
        ],
    }


_STYLE = {
    "style_name": "电影感实拍·冷蓝",
    "style_cn": "实拍电影感,冷蓝主色,自然光,胶片颗粒",
    "style_en": "cinematic live footage, cold blue tones, film grain",
    "negative": "文字,水印",
}

_HEAD = {
    **_STYLE,
    "takes": [_take("未说出口的道歉"), _take("删掉的聊天记录"), _take("空椅子")],
}

# 分镜故意漏画风锚 → 验证引擎兜底注入
_EXPAND = _expand("空教室全景(故意漏画风锚)")


def test_clips_generic_flow(client):
    """通用流:创建校验 → 批产(归一化/兜底/切段) → 三选一 → 导出。"""
    headers = _auth(client, "clips_user")
    other = _auth(client, "clips_other")

    # 校验:无主题 400 / 非法时长 400 / 非法方向 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "", "custom_theme": "", "duration_s": 15}).status_code == 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "regret", "duration_s": 20}).status_code == 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "regret", "duration_s": 15, "direction": "pixel"}).status_code == 400

    r = client.post("/api/clips", headers=headers, json={
        "theme": "regret", "duration_s": 15, "direction": "live",
        "inspiration": "异地恋的最后一通电话",
    })
    assert r.status_code == 200, r.text
    cid = r.json()["clip_row"]["id"]

    # 归属隔离
    assert client.get(f"/api/clips/{cid}", headers=other).status_code == 404

    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    candidates = job["result"]["candidates"]
    assert len(candidates) == 3
    # 两段式:①一发定风格与切入,②每条切入各一发(不再是一次大调用)
    assert _PhaseAdapter.calls == {"takes": 1, "expand": 3}
    c0 = candidates[0]
    # 15s → 镜头上限 5(两格都保留);总时长 8+7=15 切一段
    assert len(c0["shots"]) == 2
    assert len(c0["chunks"]) == 1 and c0["chunks"][0]["duration_s"] == 15
    # 镜头1 故意漏画风锚 → 引擎兜底注入
    assert "实拍电影感" in c0["shots"][0]["prompt_cn"]
    assert "cinematic live footage" in c0["shots"][0]["prompt_en"]
    # 负面基座并入
    assert c0["shots"][1]["negative"].startswith("文字,水印")
    assert c0["punchline"]

    # 三选一
    r = client.post(f"/api/clips/{cid}/pick", headers=headers, json={"index": 1})
    assert r.status_code == 200
    row = r.json()["clip_row"]
    assert row["chosen"] == 1 and row["status"] == "picked"
    assert row["clip"]["take"] == "删掉的聊天记录"
    # 无效序号被参数校验拦截
    assert client.post(f"/api/clips/{cid}/pick", headers=headers,
                       json={"index": 9}).status_code == 422

    # 导出
    r = client.get(f"/api/clips/{cid}/export?format=md", headers=headers)
    assert r.status_code == 200
    assert "情绪短片手卡" in r.text and "金句字幕卡" in r.text and "生成切段" in r.text
    r = client.get(f"/api/clips/{cid}/export?format=srt", headers=headers)
    assert "1\n00:00:00,000 --> 00:00:08,000\n有些话,隔了十年才说。" in r.text


def test_clips_novel_derived_grounding(client):
    """小说衍生:无定稿章节报错;金句不在正文 → cautions 溯源标注;在正文 → 无警示。"""
    headers = _auth(client, "clips_novel")
    r = client.post("/api/projects", headers=headers, json={"title": "投流书"})
    pid = r.json()["id"]

    # 没有定稿章节 → job error 引导
    r = client.post("/api/clips", headers=headers, json={
        "theme": "regret", "duration_s": 15, "source_project_id": pid,
    })
    cid = r.json()["clip_row"]["id"]
    r = client.post(f"/api/clips/{cid}/generate", headers=headers)
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "定稿" in job["error"]

    # 种一章定稿正文
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    with SessionLocal() as s:
        s.add(Chapter(project_id=pid, chapter_number=1, status="approved",
                      final_content="他把她的遗书读了二十年,纸角都磨圆了。"))
        s.commit()

    # 候选1 金句不在正文(编造) → 溯源警示;候选2 引用原句 → 干净
    head = {**_STYLE, "takes": [
        _take("编造金句", quote="这句在正文里根本不存在"),
        _take("真引用", quote="纸角都磨圆了"),
    ]}
    expand = _expand("空教室全景,含实拍电影感,冷蓝主色,自然光,胶片颗粒")
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(head, expand)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cands = job["result"]["candidates"]
    assert any("未在正文节选中找到" in c for c in cands[0]["cautions"])
    assert cands[1]["cautions"] == []
    # 列表按项目过滤
    r = client.get(f"/api/clips?project_id={pid}", headers=headers)
    assert [c["id"] for c in r.json()["clips"]] == [cid]


def test_one_take_failure_still_delivers_the_rest(client):
    """②三发并行:一发展开失败,另外两个本子照样交付(旧实现是一截断全批白跑)。"""
    headers = _auth(client, "clips_partial")
    r = client.post("/api/clips", headers=headers,
                    json={"theme": "regret", "duration_s": 15, "direction": "live"})
    cid = r.json()["clip_row"]["id"]

    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND, fail_takes={"空椅子"})):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cands = job["result"]["candidates"]
    assert [c["take"] for c in cands] == ["未说出口的道歉", "删掉的聊天记录"]
    # 失败那条重试过:3 条切入 → 2 成功各 1 发 + 1 失败重试 2 发
    assert _PhaseAdapter.calls == {"takes": 1, "expand": 4}


def test_too_few_candidates_reports_error(client):
    """只剩 1 个本子就没得「三选一」了,按失败上报而不是交付残次品。"""
    headers = _auth(client, "clips_toofew")
    r = client.post("/api/clips", headers=headers,
                    json={"theme": "regret", "duration_s": 15, "direction": "live"})
    cid = r.json()["clip_row"]["id"]

    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND,
                                          fail_takes={"删掉的聊天记录", "空椅子"})):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "过少" in job["error"]


def test_style_card_is_shared_by_all_candidates(client):
    """三个本子共用①定下的那一套画风锚(三选一后提示词口径必须一致)。"""
    headers = _auth(client, "clips_style")
    r = client.post("/api/clips", headers=headers,
                    json={"theme": "regret", "duration_s": 15, "direction": "live"})
    cid = r.json()["clip_row"]["id"]

    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    row = client.get(f"/api/clips/{cid}", headers=headers).json()["clip_row"]
    assert row["style_cn"] == _STYLE["style_cn"]
    for c in job["result"]["candidates"]:
        assert _STYLE["style_cn"] in c["shots"][0]["prompt_cn"]


def test_norm_shots_falls_back_to_action_when_prompt_missing():
    """LLM 漏写画面提示词:用分镜自身的景别/运镜/动作兜底,别交给画风锚造出
    一条「只有风格锚、没有画面内容」的提示词。"""
    from app.engines.clips.batch import _norm_shots

    shots = _norm_shots(
        [{"action_desc": "她低头", "shot_type": "近景", "camera": "固定", "duration_s": 3}],
        {"style_cn": "国风", "style_en": "ink", "negative": ""},
        5,
    )
    assert shots[0]["prompt_cn"] == "【画风锚】国风。近景/固定:她低头"


def test_norm_takes_blanks_generic_placeholder_quote():
    """通用入口的占位文案「(通用入口留空)」不许被模型逐字回填后原样入库
    (会跟着导出手卡印出「金句原句:(通用入口留空)」)。"""
    from app.engines.clips.batch import _norm_takes

    takes = _norm_takes(
        {"takes": [
            {"logline": "x", "quote_source": "(通用入口留空)"},
            {"logline": "y", "quote_source": "「回头」他说"},
        ]}
    )
    assert takes[0]["quote_source"] == ""
    assert takes[1]["quote_source"] == "「回头」他说"  # 真金句不动


def test_delete_rejected_while_generating(client):
    """生成中拒绝删除:任务收尾 UPDATE 这一行,行没了会 StaleDataError,
    几分钟批产白跑还报一条费解的错(线上实测 21 分钟后崩在收尾)。"""
    from app import jobs as jobs_mod

    headers = _auth(client, "clips_delguard")
    r = client.post("/api/clips", headers=headers,
                    json={"theme": "regret", "duration_s": 15, "direction": "live"})
    cid = r.json()["clip_row"]["id"]

    kind = f"clips-gen-{cid}"
    jid = f"fake{cid:04d}"
    with jobs_mod._LOCK:
        jobs_mod._JOBS[jid] = {
            "kind": kind, "status": "running", "owner_id": None,
            "stage": "生成中", "result": None, "error": None,
        }
    try:
        r = client.delete(f"/api/clips/{cid}", headers=headers)
        assert r.status_code == 409
        assert "正在生成" in r.json()["detail"]
        # 任务结束后可正常删除
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(jid, None)
        assert client.delete(f"/api/clips/{cid}", headers=headers).status_code == 200
    finally:
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(jid, None)


# ---------- 导向维度(细化"方向")与提示词强化 ----------

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


def _mk_clip(client, headers, extra=None):
    body = {"theme": "healing", "duration_s": 15, "direction": "watercolor"}
    body.update(extra or {})
    r = client.post("/api/clips", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["clip_row"]["id"]


def test_steering_fields_roundtrip_and_injection(client):
    """四个导向维度:建卡回显、非法值 400、硬约束与氛围关键词进两段提示词,
    结构铁律与俗套黑名单必须出现在正文里(早年只写在 docstring,模型看不见)。"""
    headers = _auth(client, "clips_steer")
    r = client.post("/api/clips", headers=headers, json={
        "theme": "healing", "duration_s": 15, "direction": "watercolor",
        "dialogue_style": "silent", "pacing": "twist_end", "intensity": "restrained",
        "style_hints": "雨夜便利店、暖光"})
    assert r.status_code == 200
    row = r.json()["clip_row"]
    assert row["dialogue_style"] == "silent"
    assert row["pacing"] == "twist_end"
    assert row["intensity"] == "restrained"
    assert row["style_hints"] == "雨夜便利店、暖光"

    r = client.post("/api/clips", headers=headers, json={
        "theme": "healing", "duration_s": 15, "direction": "live", "dialogue_style": "nope"})
    assert r.status_code == 400

    cid = row["id"]
    _CaptureAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers, json={})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    takes_prompt = _CaptureAdapter.prompts[0]
    assert "无台词" in takes_prompt
    assert "结尾反转" in takes_prompt
    assert "克制留白" in takes_prompt
    assert "雨夜便利店、暖光" in takes_prompt
    assert "结构铁律" in takes_prompt and "第一格 2 秒内钩住" in takes_prompt
    assert "俗套黑名单" in takes_prompt
    # ②的展开提示词同样带铁律与导向块(与①同一份 context)
    assert "结构铁律" in _CaptureAdapter.prompts[1]
    assert "无台词" in _CaptureAdapter.prompts[1]


def test_generate_feedback_reaches_takes_prompt(client):
    """换一批带意见:上一批切入摘要 + 用户意见进①提示词,这批避开旧方向。"""
    headers = _auth(client, "clips_fb")
    cid = _mk_clip(client, headers)

    _CaptureAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
        base = len(_CaptureAdapter.prompts)
        r = client.post(f"/api/clips/{cid}/generate", headers=headers,
                        json={"feedback": "金句太鸡汤,要更扎心的"})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    second_takes = _CaptureAdapter.prompts[base]
    assert "用户意见" in second_takes and "金句太鸡汤" in second_takes
    assert "上一批" in second_takes and "未说出口的道歉" in second_takes
    assert "用户意见" not in _CaptureAdapter.prompts[0]  # 首跑无反馈块


def test_reexpand_replaces_target_only(client):
    """单条重拍:只换目标条的分镜,切入/画风与其他候选不动;重拍已选条则重置三选一。"""
    headers = _auth(client, "clips_reexp")
    cid = _mk_clip(client, headers)
    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    row = client.get(f"/api/clips/{cid}", headers=headers).json()["clip_row"]
    before0 = row["candidates"][0]
    assert client.post(f"/api/clips/{cid}/pick", headers=headers, json={"index": 1}).status_code == 200

    _CaptureAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_CaptureAdapter(_HEAD, _expand("重拍后的分镜锚"))):
        r = client.post(f"/api/clips/{cid}/reexpand", headers=headers,
                        json={"index": 1, "feedback": "台词砍半"})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    # 重拍意见进了②的提示词;①没被重跑
    assert any("用户对本条的意见" in p and "台词砍半" in p for p in _CaptureAdapter.prompts)
    assert _CaptureAdapter.calls["takes"] == 0

    row = client.get(f"/api/clips/{cid}", headers=headers).json()["clip_row"]
    assert "重拍后的分镜锚" in row["candidates"][1]["shots"][0]["prompt_cn"]
    assert row["candidates"][0]["shots"][0]["prompt_cn"] == before0["shots"][0]["prompt_cn"]
    # 重拍的是已选条(原 chosen=1):重置为未选,必须重新三选一
    assert row["chosen"] == -1 and row["status"] == "generated"


def test_save_clip_card_recalc_and_sync(client):
    """手卡编辑保存:归一化入库、切段按新分镜重算、候选同步;未选定时拒绝。"""
    headers = _auth(client, "clips_save")
    cid = _mk_clip(client, headers)
    r = client.put(f"/api/clips/{cid}/clip", headers=headers, json={"card": {"shots": []}})
    assert r.status_code == 400  # 未选定不能编辑

    _PhaseAdapter.reset()
    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers, json={})
        assert _wait_job(client, headers, r.json()["job_id"])["status"] == "done"
    assert client.post(f"/api/clips/{cid}/pick", headers=headers, json={"index": 0}).status_code == 200

    card = client.get(f"/api/clips/{cid}", headers=headers).json()["clip_row"]["clip"]
    card["punchline"] = "改过的金句。"
    card["shots"] = card["shots"][:1]
    card["shots"][0]["duration_s"] = 4
    card["lines"] = [{"speaker": "旁白", "text": "改过的台词", "action": ""}]
    r = client.put(f"/api/clips/{cid}/clip", headers=headers, json={"card": card})
    assert r.status_code == 200, r.text
    row = r.json()["clip_row"]
    saved = row["clip"]
    assert saved["punchline"] == "改过的金句。"
    assert len(saved["shots"]) == 1 and saved["shots"][0]["duration_s"] == 4
    # 切段按新分镜重算:一格 4s → 一段
    assert len(saved["chunks"]) == 1 and saved["chunks"][0]["end_s"] == 4
    # 候选与手卡保持同步
    assert row["candidates"][0]["punchline"] == "改过的金句。"
