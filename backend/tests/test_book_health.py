# tests/test_book_health.py
# -*- coding: utf-8 -*-
"""成书体检报告(docs/15 §7.3):把散在各页签的质量信号收成一份报告。

验证六块内容各自的算法与**缺失语义**:
- 体量/完成度
- 质感曲线(逐章 AI 味)+ 最差三章
- 节奏曲线(逐章张力均值/峰值 + 无起伏章识别)
- 一致性(open 问题按类型分组、涉及章号)
- 伏笔健康度(四态分布、逾期未收、债务比)
- 成本(本机全库口径,token 按章均摊)

**不谎报**是重点:没数据的块必须留空并给口径说明,不能填 0 冒充「没问题」。

用隔离内存库(不碰共享库,避免弄坏 FTS5 等由迁移建的虚表)。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401 — 注册全部模型
from app.db.base import Base
from app.db.models import (
    Chapter,
    ChapterIssue,
    Foreshadowing,
    LlmUsage,
    Outline,
    Project,
    Scene,
)
from app.engines.book_health import book_health, render_health


def _db(chapters: int = 0, planned: int = 0):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(title="体检测试书", target_chapters=10)
    db.add(project)
    db.flush()
    for n in range(1, planned + 1):
        db.add(Outline(project_id=project.id, chapter_number=n, title=f"第{n}章"))
    for n in range(1, chapters + 1):
        db.add(Chapter(
            project_id=project.id, chapter_number=n,
            final_content="他推开门,雪落进来。" * 30,
            word_count=330, status="approved",
        ))
    db.commit()
    return db, project


# ---------------- 体量 ----------------

def test_body_counts_and_completion():
    db, p = _db(chapters=3, planned=10)
    r = book_health(db, p.id)
    assert r.chapters_written == 3
    assert r.chapters_planned == 10
    assert r.completion == 0.3
    assert r.total_words == 990
    assert r.avg_chapter_words == 330


def test_empty_project_leaves_sections_empty_not_zero():
    """空书:所有曲线留空并给口径说明,不填 0 冒充「没问题」。"""
    db, p = _db()
    r = book_health(db, p.id)
    assert r.flavor_curve == []
    assert r.tension_curve == []
    assert r.mean_flavor is None          # 不是 0.0
    assert any("质感曲线" in n for n in r.notes)
    assert any("节奏曲线" in n for n in r.notes)
    assert any("成本" in n for n in r.notes)


# ---------------- 质感 ----------------

def test_flavor_curve_covers_every_written_chapter():
    db, p = _db(chapters=4)
    r = book_health(db, p.id)
    assert [row["chapter"] for row in r.flavor_curve] == [1, 2, 3, 4]
    assert r.mean_flavor is not None
    assert len(r.worst_flavor) == 3   # 最差三章
    # 最差按分数降序
    scores = [row["score"] for row in r.worst_flavor]
    assert scores == sorted(scores, reverse=True)


def test_flavor_ignores_chapters_without_content():
    """status 是 approved 但没有正文的章不算「已成章」。"""
    db, p = _db(chapters=2)
    db.add(Chapter(project_id=p.id, chapter_number=3, final_content="", status="empty"))
    db.commit()
    r = book_health(db, p.id)
    assert r.chapters_written == 2
    assert [row["chapter"] for row in r.flavor_curve] == [1, 2]


# ---------------- 节奏 ----------------

def test_tension_curve_and_flat_chapter_detection():
    db, p = _db(chapters=3)
    # 第 1 章:有起伏(2 → 5);第 2 章:一个档(全 3)= 无起伏;第 3 章:2 档
    for seq, lvl in [(1, 2), (2, 5)]:
        db.add(Scene(project_id=p.id, outline_id=1, chapter_number=1, seq=seq,
                     tension_level=lvl))
    db.add(Scene(project_id=p.id, outline_id=2, chapter_number=2, seq=1, tension_level=3))
    db.add(Scene(project_id=p.id, outline_id=2, chapter_number=2, seq=2, tension_level=3))
    db.add(Scene(project_id=p.id, outline_id=3, chapter_number=3, seq=1, tension_level=4))
    db.commit()

    r = book_health(db, p.id)
    by_ch = {row["chapter"]: row for row in r.tension_curve}
    assert by_ch[1]["mean"] == 3.5
    assert by_ch[1]["peak"] == 5
    assert by_ch[1]["swing"] == 1.5
    assert by_ch[2]["swing"] == 0.0
    # 只有「有 >=2 场却全同档」才算无起伏;第 3 章只有 1 场,不算
    assert r.tension_flat_chapters == [2]


def test_no_scenes_yields_no_tension_curve():
    db, p = _db(chapters=2)
    r = book_health(db, p.id)
    assert r.tension_curve == []
    assert r.tension_flat_chapters == []
    assert any("未启用场景卡" in n for n in r.notes)


# ---------------- 一致性 ----------------

def test_consistency_groups_open_issues_by_type_and_chapter():
    db, p = _db(chapters=3)
    ch_ids = {c.chapter_number: c.id for c in db.query(Chapter).all()}
    db.add_all([
        ChapterIssue(chapter_id=ch_ids[1], source="gate", issue_type="state",
                     severity="blocker", description="左臂伤口忽然无踪", status="open"),
        ChapterIssue(chapter_id=ch_ids[1], source="rules", issue_type="worldrule",
                     severity="major", description="火系法术御水", status="open"),
        ChapterIssue(chapter_id=ch_ids[2], source="gate", issue_type="state",
                     severity="minor", description="天气翻转", status="open"),
        # resolved 不计入
        ChapterIssue(chapter_id=ch_ids[3], source="gate", issue_type="state",
                     severity="minor", description="已修", status="resolved"),
    ])
    db.commit()

    r = book_health(db, p.id)
    assert r.open_issues == 3
    assert r.issues_by_type == {"state": 2, "worldrule": 1}
    assert r.issue_chapters == [1, 2]


def test_consistency_isolates_other_projects_chapters():
    """反向验证:别的项目的问题不许算进来(靠 chapter_id 归属过滤)。"""
    db, p = _db(chapters=2)
    other = Project(title="别的书", target_chapters=5)
    db.add(other)
    db.flush()
    db.add(Chapter(project_id=other.id, chapter_number=1, final_content="x", status="approved"))
    db.flush()
    other_ch = db.query(Chapter).filter(Chapter.project_id == other.id).first()
    db.add(ChapterIssue(chapter_id=other_ch.id, source="gate", issue_type="state",
                        severity="blocker", description="别人的问题", status="open"))
    db.commit()

    r = book_health(db, p.id)
    assert r.open_issues == 0


# ---------------- 伏笔 ----------------

def test_foreshadow_health_and_overdue():
    db, p = _db(chapters=5)
    db.add_all([
        Foreshadowing(project_id=p.id, description="断锋刀来历", chapter_planted=1,
                      expected_payoff_chapter=3, status="planted", importance="major"),
        Foreshadowing(project_id=p.id, description="师父的死因", chapter_planted=2,
                      expected_payoff_chapter=9, status="reinforced", importance="critical"),
        Foreshadowing(project_id=p.id, description="旧信物", chapter_planted=1,
                      expected_payoff_chapter=4, payoff_chapter=4,
                      status="paid_off", importance="minor"),
    ])
    db.commit()

    r = book_health(db, p.id)
    assert r.foreshadow_total == 3
    assert r.foreshadow_by_status == {"planted": 1, "reinforced": 1, "paid_off": 1}
    # 逾期 = 已埋/强化 + 有预期 + 未回收 → 前两条都是(不管预期章是否已到,留给前端判定)
    assert len(r.overdue) == 2
    assert r.overdue[0]["expected"] == 3   # 按预期章升序
    assert r.debt_ratio == round(2 / 3, 3)


def test_foreshadow_resolved_not_counted_as_overdue():
    """反向验证:已回收的伏笔不算债务。"""
    db, p = _db(chapters=3)
    db.add(Foreshadowing(project_id=p.id, description="已收的", chapter_planted=1,
                         expected_payoff_chapter=2, payoff_chapter=2,
                         status="paid_off", importance="major"))
    db.commit()
    r = book_health(db, p.id)
    assert r.overdue == []
    assert r.debt_ratio == 0.0


# ---------------- 成本 ----------------

def test_cost_aggregates_tokens_and_averages_per_chapter():
    db, p = _db(chapters=4)
    db.add_all([
        LlmUsage(model="deepseek-v4-flash", prompt_tokens=1000, completion_tokens=500),
        LlmUsage(model="deepseek-v4-flash", prompt_tokens=3000, completion_tokens=1500),
    ])
    db.commit()
    r = book_health(db, p.id)
    assert r.prompt_tokens == 4000
    assert r.completion_tokens == 2000
    assert r.tokens_per_chapter == 1500   # 6000 / 4
    assert any("全库口径" in n for n in r.notes)


# ---------------- 渲染 ----------------

def test_render_health_produces_markdown_with_all_sections():
    db, p = _db(chapters=3, planned=10)
    db.add(Foreshadowing(project_id=p.id, description="断锋刀来历", chapter_planted=1,
                         expected_payoff_chapter=2, status="planted", importance="major"))
    db.commit()
    md = render_health(book_health(db, p.id))
    assert md.startswith("# 《体检测试书》成书体检报告")
    for section in ("## 体量", "## 质感曲线", "## 节奏曲线",
                    "## 一致性", "## 伏笔健康度", "## 成本"):
        assert section in md
    assert "断锋刀来历" in md          # 逾期伏笔浮出
    assert "完成度 30%" in md           # 完成度


def test_render_health_marks_flavor_curve_when_present():
    """有正文时质感曲线要画出条形图(而不是只报均值)。"""
    db, p = _db(chapters=5)
    md = render_health(book_health(db, p.id))
    assert "▁" in md or "█" in md or any(c in md for c in "▂▃▄▅▆▇")


def test_render_health_declares_missing_data_explicitly():
    """空书的报告必须写明「暂无」,不能静静给出看起来正常的 0。"""
    db, p = _db()
    md = render_health(book_health(db, p.id))
    assert "_暂无正文_" in md
    assert "_暂无场景卡数据_" in md
    assert "_暂无登记伏笔_" in md
    assert "_暂无记账数据_" in md
