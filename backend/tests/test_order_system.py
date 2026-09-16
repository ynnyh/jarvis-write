# tests/test_order_system.py
# -*- coding: utf-8 -*-
"""订单制(docs/20):章节订单 CRUD 与槽位注入、对账人物维度、锁定短路、骨架层。

存量行为零变化的机器证明在既有套件(全绿不动);本文件只测新增行为的契约:
- 订单 API:存草稿/确认/撤回/删除;确认版本递增、历史归档
- 槽位替换:order_slots 逐槽降级;确认订单进 _draft_prompt、蓝图行被替换
- 对账:确认订单存在才有 order_check;该来没来/不请自来/该退没退判定
- 锁定:outline lock 端点;save_blueprint 锁定章原样保留;级联跳过锁定章
- 骨架:读时补默认(老卷纲升级)、编辑/拍板/锁端点
- 灵感字段锁:locked_fields 输出回滚
"""
from __future__ import annotations

import uuid

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


def _mkproject(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": title, "target_chapters": 12, "genre": "都市"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


_ORDER_PAYLOAD = {
    "cast": {
        "entering": [{"name": "林晚", "reason": "新线索持有人登场"}],
        "present": ["陈默"],
        "exiting": [{"name": "老赵", "mode": "远行", "threads": "他手里的账本"}],
    },
    "relations": [{"from": "林晚", "to": "陈默", "before": "陌生", "after": "合作", "event": "账本交易"}],
    "beats": ["陈默接到匿名信", "林晚亮出账本", "两人达成交易"],
    "hooks": {"carry_in": [{"text": "上章末的脚步声", "must": True}], "leave": "账本少了一页"},
    "foreshadow": {"plant": ["账本缺页的秘密"]},
    "scenes": [],
    "free_directive": "全章小雨",
}


def _mk_outline(client: TestClient, headers: dict, pid: int, n: int) -> None:
    from app.db.session import SessionLocal
    from app.db.models import Outline
    from app.engines.pipeline.blueprint import _outline_content_hash

    data = {
        "title": f"第{n}章", "chapter_role": "推进", "chapter_purpose": "推进主线",
        "suspense_level": "中", "summary": f"第{n}章简述",
        "characters_involved": ["陈默"],
    }
    session = SessionLocal()
    try:
        session.add(Outline(
            project_id=pid, chapter_number=n,
            content_hash=_outline_content_hash(data), **data,
        ))
        session.commit()
    finally:
        session.close()


class TestChapterOrderApi:
    def test_order_crud_and_versions(self, client: TestClient):
        h = _auth(client, f"order-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "订单之书")
        _mk_outline(client, h, pid, 1)

        # 无订单:order 为 null,prefill 带大纲人物
        r = client.get(f"/api/projects/{pid}/chapters/1/order", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["order"] is None
        assert body["prefill"]["cast"]["present"] == ["陈默"]

        # 存草稿 → 草稿态;确认 → v2 + confirmed
        r = client.put(f"/api/projects/{pid}/chapters/1/order", headers=h, json=_ORDER_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"
        assert r.json()["version"] == 1

        r = client.post(f"/api/projects/{pid}/chapters/1/order/confirm",
                        headers=h, json=_ORDER_PAYLOAD)
        assert r.status_code == 200
        out = r.json()
        assert out["status"] == "confirmed"
        assert out["version"] == 2  # 存草稿 v1 → 确认归档 v1、落 v2

        # 再确认(无改动)不涨版本
        r = client.post(f"/api/projects/{pid}/chapters/1/order/confirm", headers=h)
        assert r.json()["version"] == 2

        # 撤回 → draft;删除 → 回到无订单
        r = client.post(f"/api/projects/{pid}/chapters/1/order/unconfirm", headers=h)
        assert r.json()["status"] == "draft"
        r = client.delete(f"/api/projects/{pid}/chapters/1/order", headers=h)
        assert r.json()["deleted"] == 1
        r = client.get(f"/api/projects/{pid}/chapters/1/order", headers=h)
        assert r.json()["order"] is None

    def test_order_payload_normalized(self, client: TestClient):
        """脏 payload 就地清洗:未知键丢弃、形状不对的兜底成空结构。"""
        h = _auth(client, f"order-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "清洗之书")
        _mk_outline(client, h, pid, 2)
        dirty = {"cast": {"entering": "不是列表"}, "beats": "也不是列表", "evil_key": 1}
        r = client.put(f"/api/projects/{pid}/chapters/2/order", headers=h, json=dirty)
        assert r.status_code == 200
        payload = r.json()["payload"]
        assert "evil_key" not in payload
        assert payload["cast"]["entering"] == []
        assert payload["beats"] == []


class TestOrderSlots:
    def test_slots_per_field_degradation(self):
        from app.engines.pipeline.order_block import order_slots

        assert order_slots(None) is None
        assert order_slots({}) is None
        # 只有节拍单 → 只替换节拍槽
        slots = order_slots({"beats": ["一拍", "二拍"]})
        assert slots == {"chapter_beats": "1. 一拍\n2. 二拍"}
        # 人物/关系/钩子/指令
        slots = order_slots({
            "cast": {"entering": [{"name": "甲", "reason": "因由"}], "present": ["乙"],
                     "exiting": [{"name": "丙", "mode": "远行", "threads": "旧账"}]},
            "relations": [{"from": "甲", "to": "乙", "before": "陌生", "after": "结盟", "event": "血誓"}],
            "hooks": {"carry_in": [{"text": "枪声", "must": True}], "leave": "人不见了"},
            "free_directive": "全程倒叙",
        })
        assert "必登场:甲(为何此时入场:因由)" in slots["characters_involved"]
        assert "在场:乙" in slots["characters_involved"]
        assert "本章退场:丙(远行;退场前须收:旧账)" in slots["characters_involved"]
        assert "甲—乙:陌生 → 结盟(触发:血誓)" in slots["order_appendix"]
        assert "承上必收" in slots["order_appendix"]
        assert "章末留钩:人不见了" in slots["order_appendix"]
        assert "作者指令(务必落实):全程倒叙" in slots["order_appendix"]

    def test_draft_prompt_uses_order(self):
        """确认订单进草稿 prompt:订单文本在,蓝图行人物/节拍被替换。"""
        from app.engines.pipeline.chapter_compose import ChapterContext, Composer

        class _O:
            chapter_number = 3
            title = "试炼"
            chapter_role = "推进"
            chapter_purpose = "目的"
            suspense_level = "中"
            foreshadowing = "蓝图伏笔"
            summary = "蓝图简述"
            characters_involved = ["蓝图人物"]
            key_items = []
            scene_location = "废墟"
            beats = ["蓝图节拍"]
            premise_beat = ""
            plot_twist_level = "★★★☆☆"

        class _P:
            target_chapters = 10
            target_words_per_chapter = 2000
            macro_plan = None
            architecture = None

            def __getattr__(self, name):  # 其余项目属性按空串兜底(prompt 只取文本)
                return ""

        ctx = ChapterContext(
            chapter_number=3, outline=_O(), next_outline=None,
            style_block="", rolling="", recent="", handoff_block="",
            hard_constraints="", known_roster="", resource_ledger="",
            foreshadow_reminders="", device_reminders="", avoid_repetition="",
            twist_prep="", deai_rules="", premise_block="",
            order={"beats": ["订单节拍"], "cast": {"entering": [], "present": ["订单人物"], "exiting": []}},
            project=_P(),
        )
        prompt = Composer(ctx)._draft_prompt("")
        assert "订单节拍" in prompt
        assert "蓝图节拍" not in prompt
        assert "订单人物" in prompt
        assert "蓝图人物" not in prompt
        # 无订单 → 同一 prompt 走蓝图行(存量行为)
        ctx.order = None
        prompt2 = Composer(ctx)._draft_prompt("")
        assert "蓝图节拍" in prompt2
        assert "订单节拍" not in prompt2


class TestOrderReconciliation:
    def test_order_check_dimensions(self, client: TestClient):
        h = _auth(client, f"recon-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "对账之书")
        _mk_outline(client, h, pid, 1)
        # 确认订单:林晚必登场、老赵退场
        r = client.post(f"/api/projects/{pid}/chapters/1/order/confirm",
                        headers=h, json=_ORDER_PAYLOAD)
        assert r.status_code == 200

        # 正文只写到陈默:林晚该来没来、老赵没写退场
        from app.db.session import SessionLocal
        from app.db.models import Chapter, Entity
        session = SessionLocal()
        try:
            session.add(Chapter(project_id=pid, chapter_number=1,
                                final_content="陈默在雨里等了一夜。", word_count=10))
            session.add(Entity(project_id=pid, entity_type="person", name="神秘人"))
            session.commit()
        finally:
            session.close()

        r = client.get(f"/api/projects/{pid}/chapters/1/reconciliation", headers=h)
        assert r.status_code == 200
        oc = r.json()["order_check"]
        assert oc is not None
        assert "林晚" in oc["missed"]
        assert "老赵" in oc["exit_missing"]
        assert oc["uninvited"] == []  # 神秘人没在正文出现,不算到场
        assert oc["beats_judged"] is False

        # 无订单的章:order_check 为 None(存量行为)
        _mk_outline(client, h, pid, 2)
        r = client.get(f"/api/projects/{pid}/chapters/2/reconciliation", headers=h)
        assert r.json()["order_check"] is None


class TestOutlineLock:
    def test_lock_endpoint_and_repave_protection(self, client: TestClient):
        from app.db.session import SessionLocal
        from app.db.models import Outline
        from app.engines.pipeline.blueprint import save_blueprint

        h = _auth(client, f"lock-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "锁定之书")
        _mk_outline(client, h, pid, 1)

        r = client.post(f"/api/projects/{pid}/outlines/1/lock?locked=true", headers=h)
        assert r.status_code == 200 and r.json()["locked"] is True

        session = SessionLocal()
        try:
            project = session.get.__self__.query.__self__  # 占位,下方直查
        except Exception:
            project = None
        session.close()

        # save_blueprint 对锁定章短路:同章号新蓝图不覆盖
        session = SessionLocal()
        try:
            from app.db.models import Project
            project = session.query(Project).filter(Project.id == pid).first()

            class _FakeProject:
                id = pid

            chapters = [{"chapter_number": 1, "title": "新标题", "summary": "全新简述"}]
            saved = save_blueprint(session, _FakeProject(), chapters)
            session.rollback()
            row = session.query(Outline).filter(
                Outline.project_id == pid, Outline.chapter_number == 1).first()
            assert row.title == "第1章"  # 锁定章原样保留
        finally:
            session.close()

        # 解锁后恢复参与
        r = client.post(f"/api/projects/{pid}/outlines/1/lock?locked=false", headers=h)
        assert r.json()["locked"] is False

    def test_cascade_skips_locked(self, client: TestClient):
        """级联对锁定章短路:结果带 skipped_locked,锁定章内容不变。"""
        from unittest.mock import patch, AsyncMock

        h = _auth(client, f"cascade-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "级联锁定之书")
        for n in (1, 2, 3):
            _mk_outline(client, h, pid, n)
        r = client.post(f"/api/projects/{pid}/outlines/2/lock?locked=true", headers=h)
        assert r.status_code == 200

        async def _fake_regenerate(db, project, source, numbers, reasons=None, tendency=None):
            from app.engines.cascade.regenerate import cascade_regenerate as real
            return await real(db, project, source, numbers, reasons=reasons, tendency=tendency)

        # LLM 调用打桩:regenerate 内 adapter.ask 返回合法蓝图文本
        fake_raw = "第3章 - 新标题\n本章定位:推进\n核心作用:推进\n悬念密度:中\n伏笔操作:无\n" \
                   "涉及人物:陈默\n关键道具:无\n场景地点:废墟\n本章简述:级联后的新简述"
        with patch("app.engines.cascade.regenerate.get_adapter_for") as g:
            g.return_value = type("_A", (), {"ask": staticmethod(AsyncMock(return_value=fake_raw))})()
            r = client.post(f"/api/projects/{pid}/outlines/cascade", headers=h, json={
                "source_chapter": 1, "chapter_numbers": [2, 3], "reasons": {}, "tendency": {},
            })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skipped_locked"] == [2]
        assert 3 in body["updated"]
        assert 2 not in body["updated"]


class TestSkeleton:
    def test_skeleton_upgrade_and_segment_ops(self, client: TestClient):
        from app.db.session import SessionLocal
        from app.db.models import Project

        h = _auth(client, f"skel-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "骨架之书")
        # 老式卷纲(无新字段):读时补默认
        session = SessionLocal()
        try:
            p = session.query(Project).filter(Project.id == pid).first()
            p.macro_plan = [{"start": 1, "end": 6, "goal": "第一段目标"},
                            {"start": 7, "end": 12, "goal": "第二段目标"}]
            session.commit()
        finally:
            session.close()

        r = client.get(f"/api/projects/{pid}/skeleton", headers=h)
        assert r.status_code == 200
        segs = r.json()["segments"]
        assert len(segs) == 2
        assert segs[0]["goal"] == "第一段目标"
        assert segs[0]["confirmed"] is False  # 没铺蓝图,默认未拍板
        assert segs[0]["locked"] is False

        # 编辑 / 拍板 / 锁
        r = client.put(f"/api/projects/{pid}/skeleton/0", headers=h,
                       json={"title": "初入局", "conflict": "新旧势力碰撞"})
        assert r.json()["segment"]["title"] == "初入局"
        r = client.post(f"/api/projects/{pid}/skeleton/0/confirm?confirmed=true", headers=h)
        assert r.json()["segment"]["confirmed"] is True
        r = client.post(f"/api/projects/{pid}/skeleton/0/lock?locked=true", headers=h)
        assert r.json()["segment"]["locked"] is True

        # 铺未确认段 → 400
        r = client.post(f"/api/projects/{pid}/pave-async?segment=1", headers=h,
                        json={"tendency": {}})
        assert r.status_code == 400

    def test_skeleton_generation_preserves_locked(self, client: TestClient):
        """重出骨架:locked 段原样保留(铁律 2),新段不与锁定段重叠。"""
        import json as _json
        from unittest.mock import patch, AsyncMock

        from app.db.session import SessionLocal
        from app.db.models import Project

        h = _auth(client, f"skel2-{uuid.uuid4().hex[:6]}")
        pid = _mkproject(client, h, "骨架锁书")
        session = SessionLocal()
        try:
            from app.db.models import Architecture
            p = session.query(Project).filter(Project.id == pid).first()
            p.architecture = Architecture(
                project_id=pid, core_seed="种子", character_dynamics="人物",
                world_building="世界观", plot_architecture="情节",
            )
            p.macro_plan = [{"start": 1, "end": 6, "goal": "已锁段", "confirmed": True, "locked": True}]
            session.commit()
        finally:
            session.close()

        fake = _json.dumps({"segments": [
            {"title": "新生", "goal": "g1", "conflict": "c1", "start_state": "s1", "end_state": "e1"},
            {"title": "深入", "goal": "g2", "conflict": "c2", "start_state": "s2", "end_state": "e2"},
        ]}, ensure_ascii=False)
        with patch("app.api.projects.skeleton.get_adapter_for") as g:
            g.return_value = type("_A", (), {"ask": staticmethod(AsyncMock(return_value=fake))})()
            r = client.post(f"/api/projects/{pid}/skeleton-async", headers=h, json={"tendency": {}})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        # 轮询到任务结束
        import time
        for _ in range(50):
            jr = client.get(f"/api/jobs/{job_id}", headers=h)
            if jr.json().get("status") != "running":
                break
            time.sleep(0.05)
        assert jr.json()["status"] == "done", jr.text
        segs = jr.json()["result"]["segments"]
        locked = [s for s in segs if s.get("locked")]
        assert locked and locked[0]["goal"] == "已锁段"
        # 新段从锁定段之后起,不重叠
        for s in segs:
            if not s.get("locked"):
                assert s["start"] > 6


class TestInspireFieldLocks:
    def test_locked_fields_roll_back(self, client: TestClient):
        """模型越界改了已锁字段 → 服务端强制还原;未锁字段正常放行。"""
        from tests.test_premise_dossier import _FakeAdapter  # 复用桩
        from app.api.inspire import RefineRequest, _refine_impl
        from app.schemas.concept import Concept
        from app.schemas.tendency import Tendency
        from unittest.mock import patch

        import asyncio

        concept = Concept(logline="旧的一句话故事", protagonist="旧主角")
        fake = _FakeAdapter('{"concept": {"logline": "新的一句话故事", "protagonist": "越界改的主角"}, "changed": ["logline", "protagonist"]}')
        with patch("app.api.inspire.get_adapter_for", return_value=fake):
            resp = asyncio.run(_refine_impl(RefineRequest(
                concept=concept, directive="改一下", tendency=Tendency(),
                dna=None, locked_fields=["protagonist"],
            )))
        assert resp.concept.protagonist == "旧主角"  # 锁字段被回滚
        assert resp.concept.logline == "新的一句话故事"  # 未锁字段放行
        assert "protagonist" not in resp.changed
        assert "logline" in resp.changed
        # 锁注入进 prompt
        assert "字段锁" in fake.prompts[0]
