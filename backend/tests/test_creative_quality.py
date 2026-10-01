"""参考驱动创作的行为验收：证据、归属、版本、筛选重试及剧本到分镜不丢对白。"""
import json
import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.engines.creative import analyze_references, render_goal, select_candidates
from app.engines.tendency.assembler import assemble_tendency, render_style_block
from app.schemas.creative import GoalInput


class Adapter:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return json.dumps(next(self.replies), ensure_ascii=False)


def auth(c, name):
    r = c.post("/api/auth/register", json={"username": name, "password": "pass123", "invite_code": "test-invite"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def wait(c, h, jid):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = c.get(f"/api/jobs/{jid}", headers=h).json()
        if job["status"] != "running":
            return job
        time.sleep(.02)
    raise AssertionError("job timed out")


GOAL = {"intent": "日常对白喜剧，不要打脸", "form": "sketch", "enabled": True, "expected_version": 0,
        "references": [{"name": "用户自写片段", "description": "认真争便宜却吃亏", "excerpt": "我帮你省了两块，但停车费八块。"}],
        "selected": ["engine", "voice"], "observations": [
            {"dimension": "engine", "instruction": "占便宜的动机制造反向代价", "source_index": 0, "basis": "excerpt", "evidence": "停车费八块"},
            {"dimension": "structure", "instruction": "每集五次打脸", "source_index": 0, "basis": "inferred"}], "unknowns": []}


def test_evidence_must_be_located_and_name_only_cannot_be_read():
    goal = GoalInput(references=[{"name": "从未见过的作品", "url": "https://example.invalid/a", "excerpt": "A说是免费，B问停车费呢。"}, {"name": "只有名字的参考", "url": "https://example.invalid/b"}])
    adapter = Adapter([{"observations": [
        {"dimension": "voice", "instruction": "简短的追问制造反差", "source_index": 0, "basis": "excerpt", "evidence": "停车费呢"},
        {"dimension": "structure", "instruction": "全季都这样", "source_index": 0, "basis": "excerpt", "evidence": "不存在的证据"},
        {"dimension": "engine", "instruction": "不应保留越界来源", "source_index": 4, "basis": "inferred"},
    ], "unknowns": []}])
    with patch("app.engines.creative.get_adapter_for", return_value=adapter):
        result = asyncio.run(analyze_references(goal))
    assert len(result["observations"]) == 2
    assert result["observations"][0]["basis"] == "excerpt"
    assert result["observations"][1]["basis"] == "inferred"
    assert result["observations"][1]["evidence"] == ""
    assert any("未读取" in u for u in result["unknowns"])


def test_selected_dimensions_and_empty_legacy_goal():
    assert render_goal(None) == ""
    assert render_goal({**GOAL, "enabled": False}) == ""
    result = render_style_block(assemble_tendency("chapter", {}, {"_creative_goal": GOAL}))
    assert "反向代价" in result
    assert "五次打脸" not in result
    assert "用户自写片段" not in result  # 参考名字/原文不作为故事事实注入


def test_semantic_filter_never_accepts_duplicate_or_incomplete_review():
    candidates = [{"kernel": "A"}, {"kernel": "B"}, {"kernel": "C"}]
    adapter = Adapter([{"reviews": [{"index": 0, "usable": True, "mechanism": "同一个机制"}, {"index": 1, "usable": True, "mechanism": "同一个机制"}], "accepted_indices": [0, 1, 1, True, 99]}])
    with patch("app.engines.creative.get_adapter_for", return_value=adapter), pytest.raises(ValueError):
        asyncio.run(select_candidates(candidates, GOAL, "开书方案"))


def script_data():
    return {"title": "免费停车", "scenes": [{"slug": "外·停车场·日", "purpose": "占便宜的反向代价", "lines": [
        {"speaker": "阿成", "text": "我帮你省了两块。", "action": "把优惠券递过去", "duration_s": 10, "pause_s": 1},
        {"speaker": "小吴", "text": "你车停哪儿？", "action": "抬头找车", "duration_s": 10, "pause_s": 1},
        {"speaker": "阿成", "text": "隔壁，免费停五分钟。", "action": "看手表", "duration_s": 10, "pause_s": 1},
        {"speaker": "小吴", "text": "排队领券二十分钟。", "action": "指队尾", "duration_s": 10, "pause_s": 1},
        {"speaker": "阿成", "text": "停车费八块。", "action": "收起优惠券", "duration_s": 10, "pause_s": 2},
        {"speaker": "小吴", "text": "再领三张就回本了。", "action": "一本正经走向队尾，阿成看住他", "duration_s": 10, "pause_s": 2},
    ]}]}


CAST = [{"name": "阿成", "role": "主角", "appearance": "青年男人蓝色外套"}, {"name": "小吴", "role": "配角", "appearance": "青年男人白色卫衣"}]


def test_goal_ownership_versions_and_old_episode_cannot_continue():
    with TestClient(app) as c:
        h = auth(c, "creative-owner")
        other = auth(c, "creative-other")
        sid = c.post("/api/anime", headers=h, json={"title": "生活喜剧"}).json()["series"]["id"]
        c.put(f"/api/anime/{sid}/cast", headers=h, json={"cast": CAST})
        eid = c.post(f"/api/anime/{sid}/episodes", headers=h, json={"premise": "停车"}).json()["episode"]["id"]
        assert c.get(f"/api/creative/anime/{sid}", headers=other).status_code == 404
        c.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=h, json={"synopsis": "省券亏停车费"})
        save = c.put(f"/api/creative/anime/{sid}", headers=h, json=GOAL)
        assert save.status_code == 200, save.text
        assert save.json()["goal"]["version"] == 1
        assert c.put(f"/api/creative/anime/{sid}", headers=h, json=GOAL).status_code == 409
        for action in ("shots", "script", "film-prompt", "confirm-synopsis"):
            assert c.post(f"/api/anime/episodes/{eid}/{action}", headers=h, json={}).status_code in (400, 409)
        assert c.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=h, json={"synopsis": "按新方向，领券省两块亏停车费八块"}).status_code == 200
        bad = script_data()
        bad["scenes"][0]["lines"][0]["speaker"] = "路人甲"
        assert c.put(f"/api/anime/episodes/{eid}/script", headers=h, json={"script": bad}).status_code == 400
        good = c.put(f"/api/anime/episodes/{eid}/script", headers=h, json={"script": script_data()})
        assert good.status_code == 200, good.text
        assert good.json()["episode"]["script"]["goal_version"] == 1
        result = c.put(f"/api/anime/episodes/{eid}/script", headers=h, json={"script": script_data()})
        assert len(result.json()["episode"]["script"]["history"]) == 1


def test_shots_auto_script_retry_and_dialogue_integrity():
    with TestClient(app) as c:
        h = auth(c, "creative-shots")
        sid = c.post("/api/anime", headers=h, json={"title": "小便宜"}).json()["series"]["id"]
        c.put(f"/api/anime/{sid}/cast", headers=h, json={"cast": CAST})
        c.put(f"/api/creative/anime/{sid}", headers=h, json=GOAL)
        eid = c.post(f"/api/anime/{sid}/episodes", headers=h, json={"premise": "停车"}).json()["episode"]["id"]
        c.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=h, json={"synopsis": "领券省两块亏停车费八块"})
        lines = script_data()["scenes"][0]["lines"]
        shots = [{"seq": i + 1, "duration_s": 10, "action_desc": l["action"], "dialogue": l["text"], "speaker": l["speaker"], "characters": [l["speaker"]]} for i, l in enumerate(lines)]
        bad = [{**s, "dialogue": "改掉包袱"} if i == 5 else s for i, s in enumerate(shots)]
        adapter = Adapter([script_data(), {"shots": bad}, {"shots": shots}])
        with patch("app.engines.anime.screenplay.get_adapter_for", return_value=adapter), patch("app.engines.anime.episodes.get_adapter_for", return_value=adapter):
            r = c.post(f"/api/anime/episodes/{eid}/shots", headers=h, json={})
            assert r.status_code == 200, r.text
            job = wait(c, h, r.json()["job_id"])
        assert job["status"] == "done", job
        ep = c.get(f"/api/anime/{sid}", headers=h).json()["episodes"][0]
        assert ep["script"]["total_s"] == 60
        assert ep["shots"][-1]["dialogue"] == lines[-1]["text"]
        assert ep["shots"][0]["duration_s"] == 10
        assert len(adapter.prompts) == 3
        assert c.put(f"/api/anime/episodes/{eid}/shots", headers=h, json={"shots": bad}).status_code == 400
        changed = c.post(f"/api/anime/episodes/{eid}/confirm-synopsis", headers=h, json={"synopsis": "改成包邮多买东西"}).json()["episode"]
        assert changed["script"]["stale"] is True
        assert changed["shots"] == []


def test_episode_guests_are_isolated_and_settled_before_writing():
    with TestClient(app) as c:
        h = auth(c, "creative-guests")
        sid = c.post("/api/anime", headers=h, json={"title": "客串测试"}).json()["series"]["id"]
        c.put(f"/api/anime/{sid}/cast", headers=h, json={"cast": CAST})
        eid = c.post(f"/api/anime/{sid}/episodes", headers=h, json={}).json()["episode"]["id"]
        r = c.put(f"/api/anime/episodes/{eid}/guests", headers=h, json={"guests": [{"name": "阿梅", "appearance": "短发女服务员，红围裙"}]})
        assert r.status_code == 200, r.text
        series = c.get(f"/api/anime/{sid}", headers=h).json()
        assert len(series["series"]["cast"]) == 2
        assert series["episodes"][0]["guests"][0]["name"] == "阿梅"
        assert c.put(f"/api/anime/episodes/{eid}/guests", headers=h, json={"guests": [{"name": "阿成", "appearance": "重复"}]}).status_code == 400


def test_real_novel_entry_filter_retry_locks_and_goal_survives_tag_edit():
    with TestClient(app) as c:
        h = auth(c, "creative-plan")
        project = c.post("/api/projects", headers=h, json={"title": "验收书", "mode": "drama", "audience": "male"}).json()
        pid = project["id"]
        goal = {**GOAL, "form": "continuous"}
        assert c.put(f"/api/creative/project/{pid}", headers=h, json=goal).status_code == 200
        # 旧标签快照甚至显式带假目标，也不能覆盖参考方向。
        c.patch(f"/api/projects/{pid}", headers=h, json={"global_tendency": {"genre": "都市", "_creative_goal": {"intent": "被覆盖"}}})
        assert c.get(f"/api/creative/project/{pid}", headers=h).json()["goal"]["intent"] == GOAL["intent"]
        plans = [{"title": f"方案{i}", "kernel": f"维修工{i}被索赔", "protagonist": "阿成", "world": "县城修理铺", "arc": "来客拒付→留证→反诉", "engine": "证据反制引来同行的选择", "opening": "索赔人堵店", "payoff": "还原行车记录", "escalation": "供货商撤单", "mechanism": f"因果{i}", "opening_sample": "卷帘门还没拉起来，索赔单先塞进了门缝。", "flavor": ["现实反击"]} for i in range(3)]
        bad_review = {"reviews": [{"index": i, "usable": True, "mechanism": "同机制", "reason": "只换身份"} for i in range(3)], "accepted_indices": [0, 1, 2]}
        good_review = {"reviews": [{"index": i, "usable": True, "mechanism": f"不同因果{i}", "reason": "铺垫与代价明确"} for i in range(3)], "accepted_indices": [0, 2]}
        adapter = Adapter([{"plans": plans}, bad_review, {"plans": plans}, good_review])
        with patch("app.api.projects.plans.get_adapter_for", return_value=adapter), patch("app.engines.creative.get_adapter_for", return_value=adapter):
            r = c.post(f"/api/projects/{pid}/book-plans", headers=h, json={"mode": "drama", "topic": "维修工被索赔"})
        assert r.status_code == 200, r.text
        assert len(r.json()["plans"]) == 2
        assert "未通过" in adapter.prompts[2]
        revised = Adapter([{"title": "新名", "protagonist": "擅自换人", "payoff": "新证据"}])
        with patch("app.api.projects.plans.get_adapter_for", return_value=revised):
            r = c.post(f"/api/projects/{pid}/revise-plan", headers=h, json={"index": 0, "directive": "只改书名", "locked_fields": ["protagonist", "payoff"]})
        assert r.status_code == 200, r.text
        assert r.json()["plans"][0]["protagonist"] == "阿成"
        assert r.json()["plans"][0]["payoff"] == "还原行车记录"
        assert r.json()["plans"][1]["title"] == "方案2"
        confirmed = c.post(f"/api/projects/{pid}/plan-confirm", headers=h, json={"index": 0, "mode": "drama"})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["mode"] == "drama"
        assert confirmed.json()["audience"] == "male"
        assert "还原行车记录" in confirmed.json()["brief"]


def test_reference_analyze_job_does_not_apply_goal_and_mixed_sources_are_selected():
    with TestClient(app) as c:
        h = auth(c, "creative-analyze")
        pid = c.post("/api/projects", headers=h, json={"title": "混合参考"}).json()["id"]
        body = {**GOAL, "form": "serial", "references": [GOAL["references"][0], {"name": "悬疑片段", "excerpt": "钥匙昨晚放在桌上，今天锁却换了。"}]}
        analysis = {"observations": [
            {"dimension": "voice", "instruction": "认真说话", "source_index": 0, "basis": "description", "evidence": "认真争便宜"},
            {"dimension": "engine", "instruction": "用可观察的线索建立误判", "source_index": 1, "basis": "excerpt", "evidence": "今天锁却换了"},
        ], "unknowns": ["喜剧与悬疑的重心需要样稿确认"]}
        with patch("app.engines.creative.get_adapter_for", return_value=Adapter([analysis])):
            r = c.post(f"/api/creative/project/{pid}/analyze", headers=h, json=body)
            assert r.status_code == 200, r.text
            job = wait(c, h, r.json()["job_id"])
        assert job["status"] == "done", job
        assert c.get(f"/api/creative/project/{pid}", headers=h).json()["goal"] == {}
        parsed = job["result"]["goal"]
        assert parsed["observations"][1]["source_index"] == 1
        saved = c.put(f"/api/creative/project/{pid}", headers=h, json={**parsed, "selected": ["engine"]})
        assert saved.status_code == 200, saved.text
        rendered = render_goal(saved.json()["goal"])
        assert "误判" in rendered and "认真说话" not in rendered


def test_legacy_migration_preserves_episode_and_is_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app import migrate
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE anime_series (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("CREATE TABLE anime_episodes (id INTEGER PRIMARY KEY, synopsis TEXT)"))
        conn.execute(text("INSERT INTO anime_episodes VALUES (1, '旧简介保留')"))
    monkeypatch.setattr(migrate, "engine", engine)
    migrate._add_creative_columns()
    migrate._add_creative_columns()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT synopsis, script, creative_stale, guests FROM anime_episodes")).one()
        assert tuple(row) == ("旧简介保留", None, 0, None)
    engine.dispose()
