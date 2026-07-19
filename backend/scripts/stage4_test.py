# backend/scripts/stage4_test.py
# -*- coding: utf-8 -*-
"""阶段 4 验证:大纲级联更新引擎(mock LLM,无需 API key)。

验收(roadmap):改第 5 章大纲关键情节 → 正确列出受影响下游章节 →
用户确认后同步更新 → 不出现"改了这里那里还是旧的";已有正文的章标 stale。

用法: .venv/Scripts/python -m scripts.stage4_test
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


def make_env():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, OutlineVersion, Project
    from app.db.models.project import Architecture
    from app.engines.pipeline.blueprint import _outline_content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    p = Project(title="测试", topic="t", genre="g",
                target_chapters=8, target_words_per_chapter=500)
    db.add(p); db.commit()
    p.architecture = Architecture(project_id=p.id, core_seed="种子",
                                  character_dynamics="c", world_building="w",
                                  plot_architecture="三幕结构……")
    for n in range(1, 9):
        data = {
            "title": f"章{n}", "chapter_role": "推进", "chapter_purpose": "推进",
            "suspense_level": "中", "foreshadowing": f"伏笔{n}",
            "plot_twist_level": "★★☆☆☆", "summary": f"第{n}章:主角做了事{n}",
            "characters_involved": ["张三"], "key_items": [], "scene_location": "城",
        }
        o = Outline(project_id=p.id, chapter_number=n, **data,
                    content_hash=_outline_content_hash(data), current_version=1)
        db.add(o); db.flush()
        db.add(OutlineVersion(outline_id=o.id, version=1, snapshot=dict(data, chapter_number=n),
                              change_type="minor", change_summary="初始"))
    # 第 5、6 章已有正文
    for n in (5, 6):
        db.add(Chapter(project_id=p.id, chapter_number=n, final_content=f"第{n}章正文",
                       word_count=100, status="finalized"))
    db.commit()
    return db, p


class MockAdapter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def ask(self, prompt, system=None):
        self.calls += 1
        return self.replies.pop(0)


def get_outline(db, p, n):
    from app.db.models import Outline
    return db.query(Outline).filter_by(project_id=p.id, chapter_number=n).first()


async def main_async() -> None:
    from app.db.models import Chapter, OutlineVersion
    from app.engines.cascade import differ as differ_mod
    from app.engines.cascade import impact as impact_mod
    from app.engines.cascade import regenerate as regen_mod

    db, p = make_env()

    # ---- 1. 无实质变化 ----
    o5 = get_outline(db, p, 5)
    r = await differ_mod.apply_outline_edit(db, o5, {"summary": o5.summary})
    check("差异: 无变化短路", r["status"] == "unchanged" and o5.current_version == 1)

    # ---- 2. minor 改动(只动修饰字段,不调 LLM) ----
    adapter_never = MockAdapter([])
    with patch.object(differ_mod, "get_adapter_for", return_value=adapter_never):
        r = await differ_mod.apply_outline_edit(db, o5, {"scene_location": "新城区"})
    db.commit()
    ok = (r["change_type"] == "minor" and not r["needs_impact_analysis"]
          and adapter_never.calls == 0 and o5.current_version == 2)
    check("差异: minor 不调LLM+升版本", ok, f"v{o5.current_version}, llm调用{adapter_never.calls}")
    check("差异: minor 也标记本章正文 stale",
          db.query(Chapter).filter_by(project_id=p.id, chapter_number=5).first().is_stale)

    # ---- 3. major 改动(动情节字段 → LLM 精判) ----
    adapter_major = MockAdapter(['{"change_type": "major", "summary": "张三从活着改为死亡"}'])
    with patch.object(differ_mod, "get_adapter_for", return_value=adapter_major):
        r = await differ_mod.apply_outline_edit(
            db, o5, {"summary": "第5章:张三死了", "foreshadowing": "回收:张三之死"}
        )
    db.commit()
    ok = (r["change_type"] == "major" and r["needs_impact_analysis"]
          and "死亡" in r["change_summary"] and o5.current_version == 3)
    check("差异: major 精判+提示影响分析", ok, r["change_summary"])

    vcount = db.query(OutlineVersion).filter_by(outline_id=o5.id).count()
    check("差异: 版本快照追加", vcount == 3, f"{vcount} 个版本")

    # ---- 4. 影响分析 ----
    impact_reply = ('{"affected": ['
                    '{"chapter_number": 6, "reason": "依赖张三活着推进", "action": "regenerate"},'
                    '{"chapter_number": 7, "reason": "张三有对手戏", "action": "regenerate"},'
                    '{"chapter_number": 99, "reason": "越界章节", "action": "review"}],'
                    '"overall": "张三之死波及6-7章"}')
    with patch.object(impact_mod, "get_adapter_for",
                      return_value=MockAdapter([impact_reply])):
        report = await impact_mod.analyze_impact(db, p, o5)
    ok = ([a["chapter_number"] for a in report["affected"]] == [6, 7]
          and report["source_chapter"] == 5)
    check("影响分析: 列出受影响章+过滤越界", ok, str(report["affected"]))

    # ---- 5. 级联重生成 ----
    def bp(n, title, summary):
        return (f"第{n}章 - {title}\n本章定位:转折\n核心作用:承接张三之死\n"
                f"悬念密度:高\n伏笔操作:强化:张三之死\n认知颠覆:★★★☆☆\n"
                f"涉及人物:李四\n关键道具:无\n场景地点:墓地\n本章简述:{summary}")
    regen_adapter = MockAdapter([
        bp(6, "葬礼", "李四在葬礼上发现线索"),
        bp(7, "追凶", "李四追查凶手"),
    ])
    with patch.object(regen_mod, "get_adapter_for", return_value=regen_adapter):
        result = await regen_mod.cascade_regenerate(
            db, p, 5, [6, 7, 3], reasons={6: "依赖张三活着"}
        )
    db.commit()

    check("级联: 只更新下游勾选章", result["updated"] == [6, 7]
          and any("不在下游" in w for w in result["warnings"]), str(result["warnings"]))

    o6, o7 = get_outline(db, p, 6), get_outline(db, p, 7)
    ok = (o6.title == "葬礼" and "葬礼" in o6.summary and o6.current_version == 2
          and o7.title == "追凶" and o7.current_version == 2)
    check("级联: 下游大纲真实更新", ok, f"ch6={o6.title}, ch7={o7.title}")

    ch6 = db.query(Chapter).filter_by(project_id=p.id, chapter_number=6).first()
    check("级联: 已有正文的章标 stale", result["stale_chapters"] == [6]
          and ch6.is_stale and ch6.status == "stale")

    v6 = (db.query(OutlineVersion).filter_by(outline_id=o6.id)
          .order_by(OutlineVersion.version.desc()).first())
    check("级联: 重生成留版本快照", "级联重生成" in v6.change_summary and v6.change_type == "major")

    # ---- 6. 一致性:改后再查,下游不再是旧的 ----
    check("端到端: 下游无旧内容残留",
          "事6" not in o6.summary and "事7" not in o7.summary)


def main() -> int:
    print("=" * 56)
    print("阶段 4 验证:大纲级联更新引擎")
    print("=" * 56)
    asyncio.run(main_async())
    print("-" * 56)
    passed, total = sum(results), len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
