# backend/scripts/stage3_test.py
# -*- coding: utf-8 -*-
"""阶段 3 验证:长程一致性引擎(mock LLM,无需 API key)。

核心验收(roadmap):
  A. 时序圣经:"角色第5章受伤、第12章痊愈" → 查第8章=受伤,查第13章=痊愈
  B. 伏笔调度:埋伏笔预期第6章回收 → 第4章生成时出现在提醒里;payoff 后消失
  C. 章后抽取:LLM JSON(含 markdown 围栏)正确解析并写库
  D. 一致性检查:发现违反圣经的正文,产出问题列表
  E. 重复用词检测:高频短语进避免清单
  F. 集成:生成章节时,硬约束/伏笔提醒注入草稿 Prompt

用法: .venv/Scripts/python -m scripts.stage3_test
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append(ok)
    return ok


def make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def seed_project(db):
    from app.db.models import Project
    p = Project(title="测试", topic="t", genre="g",
                target_chapters=15, target_words_per_chapter=500)
    db.add(p); db.commit()
    return p


# ---------- A. 时序圣经 ----------
def test_temporal_bible() -> None:
    from app.engines.consistency import BibleService

    db = make_db(); p = seed_project(db)
    bible = BibleService(db, p.id)

    # 第5章:受伤
    bible.apply_extraction(5, {
        "fact_changes": [{"entity": "张三", "fact_type": "state",
                          "content": "左手重伤无法使用", "importance": "critical",
                          "replaces": None}],
    })
    # 第12章:痊愈(取代旧事实)
    bible.apply_extraction(12, {
        "fact_changes": [{"entity": "张三", "fact_type": "state",
                          "content": "左手痊愈恢复功能", "importance": "major",
                          "replaces": "左手重伤无法使用"}],
    })
    db.commit()

    at8 = [f.content for f in bible.query_facts_at(8, ["张三"])]
    at13 = [f.content for f in bible.query_facts_at(13, ["张三"])]
    at3 = [f.content for f in bible.query_facts_at(3, ["张三"])]

    check("时序圣经: 第8章=受伤", at8 == ["左手重伤无法使用"], str(at8))
    check("时序圣经: 第13章=痊愈(旧事实关区间)", at13 == ["左手痊愈恢复功能"], str(at13))
    check("时序圣经: 第3章=无事实", at3 == [], str(at3))

    block = bible.hard_constraints_block(8, ["张三"])
    check("时序圣经: 硬约束渲染", "❗" in block and "左手重伤" in block and "自第5章起" in block)

    # 别名识别
    ent = bible.find_entity("张三")
    ent.aliases = ["老三"]
    db.commit()
    at8_alias = [f.content for f in bible.query_facts_at(8, ["老三"])]
    check("时序圣经: 别名识别", at8_alias == ["左手重伤无法使用"])


# ---------- B. 伏笔调度 ----------
def test_foreshadow() -> None:
    from app.engines.consistency import ForeshadowScheduler

    db = make_db(); p = seed_project(db)
    s = ForeshadowScheduler(db, p.id)

    s.apply_ops(2, [{"op": "plant", "description": "神秘芯片的来历",
                     "expected_payoff_chapter": 6, "importance": "critical"}])
    db.commit()

    check("伏笔: 第3章未到期", s.due_foreshadowings(3) == [])
    due4 = s.due_foreshadowings(4)
    check("伏笔: 第4章进入提醒窗口(6<=4+2)", len(due4) == 1)
    check("伏笔: 第7章逾期标记", "已逾期" in s.reminder_block(7))

    s.apply_ops(5, [{"op": "reinforce", "description": "神秘芯片的来历"}])
    s.apply_ops(6, [{"op": "payoff", "description": "神秘芯片的来历"}])
    db.commit()
    f = s.db.query(__import__("app.db.models", fromlist=["Foreshadowing"]).Foreshadowing).first()
    ok = (f.status == "paid_off" and f.payoff_chapter == 6
          and f.reinforcement_chapters == [5])
    check("伏笔: 四态流转 planted→reinforced→paid_off", ok,
          f"status={f.status}, payoff={f.payoff_chapter}")
    check("伏笔: 回收后不再提醒", s.due_foreshadowings(7) == [])


# ---------- C. 抽取 JSON 解析 ----------
def test_json_parse() -> None:
    from app.engines.consistency.extractor import parse_llm_json

    fenced = '```json\n{"issues": [{"severity": "major"}]}\n```'
    check("JSON: markdown 围栏", parse_llm_json(fenced) == {"issues": [{"severity": "major"}]})
    noisy = '好的,以下是结果:\n{"a": 1}\n希望有帮助'
    check("JSON: 前后噪音截取", parse_llm_json(noisy) == {"a": 1})
    check("JSON: 坏输入返回空dict", parse_llm_json("完全不是json") == {})


# ---------- D. 一致性检查 ----------
async def test_checker() -> None:
    from app.engines.consistency import BibleService
    from app.engines.consistency import checker as checker_mod

    db = make_db(); p = seed_project(db)
    BibleService(db, p.id).apply_extraction(1, {
        "fact_changes": [{"entity": "张三", "fact_type": "state",
                          "content": "左手已截肢", "importance": "critical",
                          "replaces": None}]})
    db.commit()

    class MockAdapter:
        async def ask(self, prompt, system=None):
            assert "左手已截肢" in prompt  # 硬约束进了检查 prompt
            return ('{"issues": [{"severity": "critical", "type": "state", '
                    '"description": "张三用左手接住了刀", '
                    '"conflicting_fact": "左手已截肢", "suggestion": "改为右手"}]}')

    with patch.object(checker_mod, "get_adapter_for", return_value=MockAdapter()):
        issues = await checker_mod.check_chapter(db, p.id, 2, "张三用左手接住了刀……")
    ok = len(issues) == 1 and issues[0]["severity"] == "critical"
    check("一致性检查: 发现违反圣经的矛盾", ok, str(issues[:1]))

    # 空圣经 → 不调 LLM 直接空
    db2 = make_db(); p2 = seed_project(db2)
    issues2 = await checker_mod.check_chapter(db2, p2.id, 1, "任意正文")
    check("一致性检查: 空圣经跳过", issues2 == [])


# ---------- E. 重复用词 ----------
def test_repetition() -> None:
    from app.engines.consistency.repetition import avoid_block, find_repeated_phrases

    texts = ["他心如刀绞地看着。" * 2, "她心如刀绞地哭了。心如刀绞的感觉。"]
    rep = find_repeated_phrases(texts)
    ok = any("心如刀绞" in g for g, c in rep)
    check("重复检测: 识别高频短语", ok, str(rep[:3]))
    check("重复检测: 无重复返回空块", avoid_block(["完全不同的一句话。"]) == "")


# ---------- F. 集成:注入草稿 Prompt ----------
async def test_integration() -> None:
    from app.db.models import Outline
    from app.db.models.project import Architecture
    from app.engines.consistency import BibleService, ForeshadowScheduler
    from app.engines.pipeline import chapter as ch_mod

    db = make_db(); p = seed_project(db)
    p.architecture = Architecture(project_id=p.id, core_seed="s",
                                  character_dynamics="c", world_building="w",
                                  plot_architecture="pl")
    db.add(Outline(project_id=p.id, chapter_number=4, title="对决",
                   chapter_role="高潮", chapter_purpose="冲突爆发",
                   summary="第4章", characters_involved=["张三"], key_items=[],
                   scene_location="废墟", content_hash="h", current_version=1))
    db.commit()

    BibleService(db, p.id).apply_extraction(2, {
        "fact_changes": [{"entity": "张三", "fact_type": "state",
                          "content": "左手已截肢", "importance": "critical",
                          "replaces": None}]})
    ForeshadowScheduler(db, p.id).apply_ops(1, [
        {"op": "plant", "description": "断刀的秘密", "expected_payoff_chapter": 5}])
    db.commit()

    class MockAdapter:
        def __init__(self): self.prompts = []
        async def ask(self, prompt, system=None):
            self.prompts.append(prompt)
            return "正文内容。" * 20 if "现在开始写" in prompt or "修订后的" in prompt else "摘要"

    adapter = MockAdapter()

    async def no_add(self, n, t): return 0
    async def no_ret(self, q, **k): return []
    async def no_check(*a, **k): return []
    async def no_extract(*a, **k): return {"bible": {"facts": 1}}

    with patch.object(ch_mod, "get_adapter_for", return_value=adapter), \
         patch.object(ch_mod.ChapterMemory, "add_chapter", no_add), \
         patch.object(ch_mod.ChapterMemory, "retrieve", no_ret), \
         patch.object(ch_mod, "check_chapter", no_check), \
         patch.object(ch_mod, "extract_and_apply", no_extract):
        _c, issues, stats, _guard = await ch_mod.generate_chapter(db, p, 4)

    draft_prompt = adapter.prompts[0]
    ok = ("左手已截肢" in draft_prompt and "断刀的秘密" in draft_prompt
          and "硬约束" in draft_prompt)
    check("集成: 硬约束+伏笔提醒注入草稿 Prompt", ok)
    check("集成: 返回抽取统计", stats == {"bible": {"facts": 1}})


def main() -> int:
    print("=" * 56)
    print("阶段 3 验证:长程一致性引擎")
    print("=" * 56)
    test_temporal_bible()
    test_foreshadow()
    test_json_parse()
    asyncio.run(test_checker())
    test_repetition()
    asyncio.run(test_integration())
    print("-" * 56)
    passed, total = sum(results), len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
