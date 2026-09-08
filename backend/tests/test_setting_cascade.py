# tests/test_setting_cascade.py
# -*- coding: utf-8 -*-
"""设定级级联:规则 diff → 影响扫描(粗筛+定位)→ 定点修提案(mock LLM)。

覆盖:
- diff_rules:增/删/改/无差异四种基本形态(确定性,零 token)
- scan_setting_impact:章级粗筛命中 → 段级定位引文反查段号;幻觉引文丢弃
  (unlocated 计数);粗筛解析失败显式计 failed(绝不静默当"不受影响")
- patch_passages:提案结构 {para_idx, old, new, notes, ok};段号越界 → stale
- 接口层:无差异 400 / 缺 passages 400
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.engines.setting_cascade import diff_rules

CH_TEXT = (
    "晚自习时,林涛翻开政治课本开始背诵,为后天的政治高考做准备。\n"
    "同桌笑他:理科生背什么政治。\n"
    "林涛没理他,继续划重点。"
)

RULES_OLD = "林涛是理科生,不考政治。"
RULES_NEW = "林涛是文科生,政治是他的强项。"


def _make_db():
    """独立内存库:一个项目 + 一章大纲(带概要)+ 一章正文。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="设定级联测试书", target_chapters=3, target_words_per_chapter=3000,
        world_rules=RULES_OLD,
    )
    db.add(project)
    db.flush()
    db.add(Outline(
        project_id=project.id, chapter_number=1, title="晚自习",
        chapter_purpose="推进主线", summary="备考日常", current_version=1,
    ))
    db.flush()
    db.add(Chapter(
        project_id=project.id, outline_id=1, chapter_number=1,
        final_content=CH_TEXT, word_count=len(CH_TEXT), status="approved",
    ))
    db.commit()
    return db, project


class _SeqAdapter:
    """按序回放回复;记录全部 prompt 供断言。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


def _run_scan(db, project_id, changes, replies):
    from app.engines import setting_cascade as sc

    adapter = _SeqAdapter(replies)
    with patch.object(sc, "get_adapter_for", return_value=adapter):
        result = asyncio.run(
            sc.scan_setting_impact(db, project_id, changes)
        )
    return result, adapter


# ---------- diff_rules ----------

def test_diff_rules_kinds():


    assert diff_rules(RULES_OLD, RULES_NEW) == [
        {"kind": "changed", "old": RULES_OLD, "new": RULES_NEW}
    ]
    # 纯新增
    assert diff_rules("", "规则A\n规则B") == [
        {"kind": "added", "old": "", "new": "规则A"},
        {"kind": "added", "old": "", "new": "规则B"},
    ]
    # 纯删除
    assert diff_rules("规则A\n规则B", "规则A") == [
        {"kind": "removed", "old": "规则B", "new": ""}
    ]
    # 无差异(仅空白差异)
    assert diff_rules("规则A", "  规则A  \n") == []


# ---------- 影响扫描 ----------

def test_scan_screens_and_locates_with_backend_quote_lookup():
    """粗筛命中 → 定位引文反查段号(段号由后端定,不信任模型数数)。"""
    db, project = _make_db()
    changes = diff_rules(RULES_OLD, RULES_NEW)
    locate_reply = json.dumps({"hits": [
        # 第 0 段逐字引文(含在 CH_TEXT 第一段里)
        {"quote": "翻开政治课本开始背诵", "reason": "文科生设定下背政治合理,但需同步同桌台词"},
        # 第 1 段引文(跨模型常加的空格/换行也能兜住)
        {"quote": "同桌笑他: 理科生背什么政治。", "reason": "与新设定冲突"},
        # 幻觉引文:正文没有 → 丢弃
        {"quote": "这段话正文里根本不存在呀", "reason": "幻觉"},
    ]}, ensure_ascii=False)
    result, adapter = _run_scan(db, project.id, changes, [
        json.dumps({"affected": True, "reason": "备考政治与新设定相关"}, ensure_ascii=False),
        locate_reply,
    ])

    assert result["screened"] == 1
    assert result["affected_chapters"][0]["chapter_number"] == 1
    assert len(result["passages"]) == 2
    idxs = {p["para_idx"] for p in result["passages"]}
    assert idxs == {0, 1}  # 段号是后端反查的
    assert result["unlocated"] == 1
    assert result["failed"] == []
    # 粗筛 prompt 带概要,定位 prompt 带 [P编号] 段落
    assert "备考日常" in adapter.prompts[0]
    assert "[P0]" in adapter.prompts[1]
    db.close()


def test_scan_parse_failure_is_explicit_not_silent_pass():
    """粗筛解析失败 → 计入 failed,绝不静默当「不受影响」。"""
    db, project = _make_db()
    result, _ = _run_scan(db, project.id, diff_rules(RULES_OLD, RULES_NEW), [
        "模型返回被截断的半截 {\"affe",  # 解析失败
    ])
    assert result["affected_chapters"] == []
    assert result["failed"] == [1]
    db.close()


def test_scan_skips_quarantined_and_empty_chapters():
    """隔离章/无正文章不参与级联。"""
    from app.db.models import Chapter

    db, project = _make_db()
    db.add(Chapter(
        project_id=project.id, outline_id=None, chapter_number=2,
        final_content="隔离章正文", word_count=6, status="quarantined",
    ))
    db.add(Chapter(
        project_id=project.id, outline_id=None, chapter_number=3,
        final_content="", word_count=0, status="drafted",
    ))
    db.commit()
    result, adapter = _run_scan(db, project.id, diff_rules(RULES_OLD, RULES_NEW), [
        json.dumps({"affected": True, "reason": "x"}, ensure_ascii=False),
        json.dumps({"hits": []}, ensure_ascii=False),
    ])
    assert result["screened"] == 1  # 只有第 1 章参与
    db.close()


# ---------- 定点修提案 ----------

def test_patch_passages_proposes_pairs_and_stale():
    from app.engines import setting_cascade as sc

    db, project = _make_db()
    changes = diff_rules(RULES_OLD, RULES_NEW)
    patch_reply = json.dumps({
        "new_paragraph": "晚自习时,林涛翻开政治笔记查漏补缺,为后天的政治考试做准备。",
        "notes": "课本改为笔记,贴合文科生设定",
    }, ensure_ascii=False)

    adapter = _SeqAdapter([patch_reply])
    passages = [
        {"chapter_number": 1, "para_idx": 0},   # 正常
        {"chapter_number": 1, "para_idx": 99},  # 越界 → stale
    ]
    with patch.object(sc, "get_adapter_for", return_value=adapter):
        result = asyncio.run(sc.patch_passages(db, project.id, changes, passages))

    assert result["total"] == 2
    assert result["stale"] == 1
    pairs = result["chapters"][0]["pairs"]
    assert pairs[0]["ok"] is True
    assert pairs[0]["para_idx"] == 0
    assert pairs[0]["old"] == CH_TEXT.split("\n")[0].strip()
    assert "文科生" not in pairs[0]["new"] or "政治" in pairs[0]["new"]
    assert pairs[1]["ok"] is False
    db.close()


# ---------- 接口层 ----------

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth(client, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_scan_async_400_on_no_diff(client):
    headers = _auth(client, "setcas_no_diff")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "级联书", "target_chapters": 3},
    ).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/setting-cascade/scan-async", headers=headers,
        json={"old_text": "规则A", "new_text": "  规则A "},
    )
    assert r.status_code == 400
    assert "没有差异" in r.json()["detail"]


def test_patch_async_400_without_passages(client):
    headers = _auth(client, "setcas_no_pass")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "级联书2", "target_chapters": 3},
    ).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/setting-cascade/patch-async", headers=headers,
        json={"changes": [{"kind": "changed", "old": "a", "new": "b"}],
              "passages": []},
    )
    assert r.status_code == 400
    assert "冲突段落" in r.json()["detail"]


# ---------- 二期:人物卡级联 ----------

PROFILE_OLD = (
    "林涛是高三理科生,性格沉默寡言,不擅长与人打交道。"
    "他暗恋同桌苏晓,但从未说出口。"
    "周末在 HackOS 论坛写技术博客。"
)
PROFILE_NEW = (
    "林涛是高三理科生,性格开朗话多,是班里的气氛担当。"
    "他暗恋同桌苏晓,但从未说出口。"
    "周末在 HackOS 论坛写技术博客。"
)


def test_diff_profile_sentence_level():
    from app.engines.setting_cascade import diff_profile

    changes = diff_profile(PROFILE_OLD, PROFILE_NEW)
    # 三句只改了第一句:句级切分让变更粒度清晰,不会整段算一条
    assert changes == [{
        "kind": "changed",
        "old": "林涛是高三理科生,性格沉默寡言,不擅长与人打交道。",
        "new": "林涛是高三理科生,性格开朗话多,是班里的气氛担当。",
    }]
    # 无差异(仅空白)→ 空
    assert diff_profile(PROFILE_OLD, f"  {PROFILE_OLD} ") == []


def _mk_char(client, headers, pid: int, profile: str) -> int:
    r = client.post(
        f"/api/projects/{pid}/characters", headers=headers,
        json={"name": "林涛", "aliases": ["涛哥"], "profile": profile},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_patch_character_profile_returns_changes_and_syncs_seed_fact(client):
    """编辑简介:返回句级 diff(带 entity),圣经登记事实同步改写。"""
    headers = _auth(client, "setcas_char_edit")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "人物卡级联书", "target_chapters": 3},
    ).json()["id"]
    cid = _mk_char(client, headers, pid, PROFILE_OLD)

    r = client.patch(
        f"/api/projects/{pid}/characters/{cid}", headers=headers,
        json={"profile": PROFILE_NEW, "aliases": ["涛哥", "小涛"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] == PROFILE_NEW
    assert body["aliases"] == ["涛哥", "小涛"]
    # 变更清单:句级 diff + entity 标注,前端原样传给扫描端点
    assert len(body["changes"]) == 1
    ch = body["changes"][0]
    assert ch["kind"] == "changed" and ch["entity"] == "林涛"
    assert "沉默寡言" in ch["old"] and "开朗话多" in ch["new"]

    # 圣经同步:登记事实(source_chapter=0)已改写为新简介
    bible = client.get(
        f"/api/projects/{pid}/bible?chapter=1", headers=headers,
    ).json()
    seed_contents = [f["content"] for f in bible["facts"]
                     if f["entity"] == "林涛" and "开朗话多" in f["content"]]
    assert seed_contents, "登记事实未同步新简介"


def test_patch_character_first_profile_no_cascade_changes(client):
    """原来没简介时补写:纯新增,不追问级联(changes 为空)。"""
    headers = _auth(client, "setcas_char_first")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "人物卡级联书2", "target_chapters": 3},
    ).json()["id"]
    cid = _mk_char(client, headers, pid, "")
    r = client.patch(
        f"/api/projects/{pid}/characters/{cid}", headers=headers,
        json={"profile": "新的简介,第一句。第二句设定。"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["changes"] == []


def test_patch_character_retire_still_works(client):
    """回归:退场/恢复的老用法不受 schema 扩展影响。"""
    headers = _auth(client, "setcas_char_retire")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "人物卡级联书3", "target_chapters": 3},
    ).json()["id"]
    cid = _mk_char(client, headers, pid, "简介。")
    r = client.patch(
        f"/api/projects/{pid}/characters/{cid}", headers=headers,
        json={"retired": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["retired"] is True
    assert r.json()["changes"] == []


def test_scan_async_accepts_preset_changes(client):
    """扫描端点吃现成变更清单(人物卡场景):不再要求 old/new。"""
    headers = _auth(client, "setcas_preset")
    pid = client.post(
        "/api/projects", headers=headers,
        json={"title": "级联书4", "target_chapters": 3},
    ).json()["id"]
    changes = [{"kind": "changed", "old": "a", "new": "b",
                "entity": "林涛"}]
    r = client.post(
        f"/api/projects/{pid}/setting-cascade/scan-async", headers=headers,
        json={"changes": changes},  # 不带 old_text/new_text
    )
    assert r.status_code == 200, r.text
    assert r.json()["job_id"]
    # 脏 changes(缺 kind)被清洗后视为无差异 → 400
    r2 = client.post(
        f"/api/projects/{pid}/setting-cascade/scan-async", headers=headers,
        json={"changes": [{"foo": 1}]},
    )
    assert r2.status_code == 400
