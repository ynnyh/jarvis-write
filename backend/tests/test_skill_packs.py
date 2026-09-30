# tests/test_skill_packs.py
# -*- coding: utf-8 -*-
"""创作 Skill 包(docs/21):seed 幂等 / 列表 / 启停 / 条目编辑版本化 / 回退 /
条目校验白名单 / 注入块渲染与预算闸 / 节点隔离。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.engines.skills.packs import BUILTIN_PACKS, VALID_SCOPES
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


def _headers(client: TestClient) -> dict:
    return _auth(client, f"skill_u_{int(time.time() * 1000) % 10 ** 9}")


def test_skill_packs_seed_and_list(client):
    headers = _headers(client)
    packs = client.get("/api/skill-packs", headers=headers).json()
    keys = {p["pack_key"] for p in packs}
    # 首批 anime 两包(docs/21)+ 漫剧源书双包(docs/23)+ 出片线六包(docs/25 §2)
    assert {
        "storyboard-basics", "anime-shotcard-render",
        "drama_source_male", "drama_source_female",
        "drama-shotcard-render", "drama-scene-punch",
        "promo-hook-3s", "promo-landmark-guard",
        "clips-emotion-curve", "clips-compact-render",
    } <= keys
    builtin = [p for p in packs if p["is_builtin"]]
    by_key = {p["pack_key"]: p for p in builtin}

    # 默认开关是设计决策,不是随手填的(docs/25 §2.2):
    #   工艺收口类默认开——要么口径本来就对只是没单点化(drama-shotcard-render),
    #   要么是作者 2026-09-28 明确拍板要默认开(clips-compact-render);
    #   内容口径类默认关——包一开就改写生成结果,默认开等于让老用户无预警换口径;
    #   爽文双包默认关——靠书级 mounted_packs 挂载,全局开反而污染普通书(docs/23)。
    # 这些断言的作用是:谁把默认开关翻过去,测试立刻变红。
    assert by_key["storyboard-basics"]["enabled"] is True
    assert by_key["anime-shotcard-render"]["enabled"] is True
    assert by_key["drama-shotcard-render"]["enabled"] is True
    assert by_key["clips-compact-render"]["enabled"] is True
    for off in ("drama-scene-punch", "promo-hook-3s", "promo-landmark-guard",
                "clips-emotion-curve", "drama_source_male", "drama_source_female"):
        assert by_key[off]["enabled"] is False, f"{off} 应默认停用"

    # 分镜功底包三线共用一份(docs/25 §2.1:同义包合并 scope,不各写一份改版)
    assert set(by_key["storyboard-basics"]["scope"]) == {"anime", "drama", "promo"}
    # 爽文包扩到漫剧线:漫剧剧本工序复用小说那套爽点工艺(docs/25 拍板 1)
    assert set(by_key["drama_source_male"]["scope"]) == {"novel", "drama"}
    assert set(by_key["drama_source_female"]["scope"]) == {"novel", "drama"}

    assert all(p["version"] >= 1 and p["history"] == [] for p in builtin)  # 版本可随官方升级推进
    # 幂等:重复拉取不重复种
    again = client.get("/api/skill-packs", headers=headers).json()
    assert len([p for p in again if p["is_builtin"]]) == len(builtin)
    # 官方包排在前面
    assert again[0]["is_builtin"] is True


def test_every_builtin_pack_scope_is_known():
    """包 scope 必须在 packs.VALID_SCOPES 白名单里。

    为什么钉:写错 scope 不报错,只是**永远不生效**——包静静躺在设置页里,
    用户开了开关也没用。这类失效只能靠测试发现。
    """
    for spec in BUILTIN_PACKS:
        assert spec["scope"], f"{spec['pack_key']} 没有 scope"
        for scope in spec["scope"]:
            assert scope in VALID_SCOPES, (
                f"{spec['pack_key']} 的 scope {scope!r} 不在白名单 {sorted(VALID_SCOPES)}")
        assert spec.get("pack_key"), "包必须有 pack_key"


def test_skill_pack_toggle_edit_restore(client):
    headers = _headers(client)
    packs = client.get("/api/skill-packs", headers=headers).json()
    pack = next(p for p in packs if p["pack_key"] == "storyboard-basics")

    # 停用 / 启用
    r = client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                     json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                        json={"enabled": True}).json()["enabled"] is True

    # 编辑条目:version+1,旧版进 history
    entries = [dict(e) for e in pack["entries"]]
    entries[0]["directive"] = "每镜至多两个主动作;镜间必须有转场设计。"
    r = client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                     json={"entries": entries})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 2
    assert body["history"] and body["history"][-1]["version"] == 1
    assert "两个主动作" in body["entries"][0]["directive"]

    # 回退 v1 内容:当前版入 history,版本继续前进(回退也是一次编辑)
    r = client.post(f"/api/skill-packs/{pack['id']}/restore", headers=headers,
                    json={"version": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 3
    assert "一个主动作" in body["entries"][0]["directive"]

    # 回退不存在的版本:404
    assert client.post(f"/api/skill-packs/{pack['id']}/restore", headers=headers,
                       json={"version": 99}).status_code == 404
    # 不存在的包:404
    assert client.patch("/api/skill-packs/999999", headers=headers,
                        json={"enabled": False}).status_code == 404


def test_skill_pack_entries_validation(client):
    """条目归一走白名单:未知 kind/缺指令文本一律 400,不许脏条目入库。"""
    headers = _headers(client)
    packs = client.get("/api/skill-packs", headers=headers).json()
    pack = packs[0]
    r = client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                     json={"entries": [{"node": "shots", "kind": "magic"}]})
    assert r.status_code == 400 and "白名单" in r.json()["detail"]
    r = client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                     json={"entries": [{"node": "shots", "kind": "directive"}]})
    assert r.status_code == 400 and "指令文本" in r.json()["detail"]
    r = client.patch(f"/api/skill-packs/{pack['id']}", headers=headers,
                     json={"entries": []})
    assert r.status_code == 400


def test_render_skill_block_node_isolation_and_budget(client):
    """注入块:按节点分发 / 按线隔离 / 停用即空 / 预算闸整包丢弃。"""
    from app.db.models import SkillPack
    from app.db.session import SessionLocal
    from app.engines.skills import packs as skill_packs

    with SessionLocal() as db:
        skill_packs.ensure_builtin_packs(db)
        block = skill_packs.render_skill_block(db, scope="anime", node="shots")
        assert "创作 Skill" in block and "分镜功底包" in block
        assert "每镜只安排一个主动作" in block  # directive 条目渲染
        assert "单镜时长上限" in block  # param 条目渲染成「键: 值」

        # 节点隔离:小说线不吃动漫包;render 节点不吃分镜包
        assert "分镜功底包" not in skill_packs.render_skill_block(db, scope="novel", node="draft")
        assert "分镜功底包" not in skill_packs.render_skill_block(db, scope="anime", node="draft")

        # 停用 → 空块(模板槽吃空串零副作用)
        pack = db.query(SkillPack).filter(SkillPack.pack_key == "storyboard-basics").first()
        pack.enabled = False
        db.commit()
        assert skill_packs.render_skill_block(db, scope="anime", node="shots") == ""
        pack.enabled = True
        db.commit()

        # 预算闸:预算收到极小,超预算的包整包丢弃(宁可少注入,不注半截)
        old = skill_packs.INJECT_CHAR_BUDGET
        skill_packs.INJECT_CHAR_BUDGET = 10
        try:
            assert skill_packs.active_packs(db, scope="anime", node="shots") == []
        finally:
            skill_packs.INJECT_CHAR_BUDGET = old


def test_normalize_entries_clamps(client):
    """条目归一:超长文本裁剪、params 键值上限、ban_list 清洗。"""
    from app.engines.skills.packs import normalize_entries

    entries = normalize_entries([
        {"node": "shots", "kind": "directive", "directive": "x" * 900},
        {"node": "shots", "kind": "param",
         "params": {f"参数{k}": "v" * 200 for k in range(15)}},
        {"node": "render", "kind": "ban", "ban_list": ["乱码", "", "水印"]},
        {"node": "render", "kind": "format", "directive": "换渲染工艺"},
        "不是字典的条目会被丢弃",
    ])
    assert len(entries[0]["directive"]) == 600
    assert len(entries[1]["params"]) == 10
    assert entries[2]["ban_list"] == ["乱码", "水印"]
    assert entries[3]["kind"] == "format"
