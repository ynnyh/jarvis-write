# app/engines/book_health.py
# -*- coding: utf-8 -*-
"""成书体检报告(docs/15 §7.3):用数字回答「这本书现在什么状态」。

为什么需要它:项目已有一堆**零散的**质量信号——概览页的 AI 味指数、审核报告的
一致性问题、伏笔看板的四态、章末契约的时间线——但它们散在四个页签里,没有任何
一处把它们**并排放在一起**回答「这本书整体健康吗、哪几章是短板」。

本模块是**只读聚合 + 导出**:把已有信号收进一份报告,不新增生成行为、不设卡口、
不调 LLM。它是对外证明的载体(docs/15 §7.3)——
「用事实,不用形容词」回答「你这系统写出来的书到底怎么样」。

六块内容(全部确定性):
  ① **体量**:已成章数 / 总字数 / 平均章长 / 完成度(已成章 ÷ 蓝图章)
  ② **质感曲线**:逐章 AI 味指数(复用 ai_flavor_report,与线上看板同口径)
  ③ **节奏曲线**:逐章张力均值与峰值(来自场景卡 tension_level;无场景卡的书为空)
  ④ **一致性**:未解决问题数(按类型分组)+ 冲突涉及的章
  ⑤ **伏笔健康度**:四态分布 + 逾期未收列表 + 长线债务比(未回收 ÷ 总数)
  ⑥ **成本**:已记录的 token 用量与按章均摊(来自 llm_usage;无记账时为空)

设计边界:
  - **本模块是叶子**:只依赖 db 模型 + 其它叶子引擎(ai_flavor / repetition),
    不 import api 层、不 import pipeline。
  - **不谎报**:任何一块数据缺失就**留空并说明原因**,不填 0 冒充「没问题」
    (0 分与「没数据」在报告里必须能区分——这是评审最容易被误导的地方)。
  - 报告同时给 `sections`(结构化,给前端画图)与 `markdown`(给人读、可下载)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# 体量/成本为 0 时不算「异常」,但要让调用方知道「这是没数据不是没问题」。
# 统一用一句话说明,避免前端各自编文案。
_NO_DATA_HINT = "暂无数据"


@dataclass
class HealthReport:
    """成书体检报告。所有字段都有默认值——缺数据的书也能安全构造。"""

    project_id: int = 0
    title: str = ""
    # ① 体量
    chapters_planned: int = 0
    chapters_written: int = 0
    total_words: int = 0
    avg_chapter_words: int = 0
    completion: float = 0.0
    # ② 质感曲线(逐章 AI 味;空列表 = 没有正文)
    flavor_curve: list[dict[str, Any]] = field(default_factory=list)
    mean_flavor: float | None = None
    worst_flavor: list[dict[str, Any]] = field(default_factory=list)
    # ③ 节奏曲线(逐章张力;空 = 该书未用场景卡)
    tension_curve: list[dict[str, Any]] = field(default_factory=list)
    tension_flat_chapters: list[int] = field(default_factory=list)
    # ④ 一致性
    open_issues: int = 0
    issues_by_type: dict[str, int] = field(default_factory=dict)
    issue_chapters: list[int] = field(default_factory=list)
    # ⑤ 伏笔健康度
    foreshadow_total: int = 0
    foreshadow_by_status: dict[str, int] = field(default_factory=dict)
    overdue: list[dict[str, Any]] = field(default_factory=list)
    debt_ratio: float = 0.0
    # ⑤+' 核心梗健康度(docs/19 M4):梗是纲,兑现断了就是文扑前兆
    premise_defined: bool = False
    premise_high_concept: str = ""
    premise_beats: list[str] = field(default_factory=list)
    premise_ledger_curve: list[dict[str, Any]] = field(default_factory=list)
    premise_unfulfilled_streak: int = 0   # 最近连续未兑现章数(越靠后越危险)
    premise_max_streak: int = 0           # 全书最长连续未兑现
    premise_uncovered_chapters: list[int] = field(default_factory=list)  # 已写但无对账账的章
    premise_fulfilled_ratio: float | None = None  # 有账章节中兑现占比
    # ⑥ 成本
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_chapter: int = 0
    # 数据可用性说明:哪几块没数据、为什么(不谎报)
    notes: list[str] = field(default_factory=list)
    markdown: str = ""


def book_health(db: Session, project_id: int) -> HealthReport:
    """聚合一本书的体检报告。只读,零 LLM。"""
    from app.db.models import Chapter, Project

    project = db.query(Project).filter(Project.id == project_id).first()
    report = HealthReport(
        project_id=project_id,
        title=(project.title if project else "") or "",
    )

    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
        .all()
    )
    written = [c for c in chapters if (c.final_content or "").strip()]
    report.chapters_written = len(written)
    report.total_words = sum(int(c.word_count or 0) for c in written)
    report.avg_chapter_words = (
        round(report.total_words / len(written)) if written else 0
    )

    _fill_completion(db, project_id, report)
    _fill_flavor(report, written)
    _fill_tension(db, project_id, report)
    _fill_consistency(db, project_id, report)
    _fill_foreshadow(db, project_id, report)
    _fill_premise(db, project_id, report)
    _fill_cost(db, report)

    report.markdown = render_health(report)
    return report


def _fill_completion(db: Session, project_id: int, report: HealthReport) -> None:
    """完成度 = 已成章 ÷ 蓝图章。蓝图章数取 outlines 的行数。"""
    from app.db.models import Outline

    planned = (
        db.query(Outline).filter(Outline.project_id == project_id).count()
    )
    report.chapters_planned = planned
    if planned:
        report.completion = round(report.chapters_written / planned, 3)
    elif report.chapters_written:
        # 旧书导入的书可能没有大纲行,只有正文
        report.completion = 1.0
        report.notes.append("无蓝图记录(可能是导入的旧书),完成度按「已写完」计")


def _fill_flavor(report: HealthReport, written: list) -> None:
    """逐章 AI 味指数(与概览看板同一函数、同一口径)。"""
    from app.engines.polish.ai_flavor import ai_flavor_report

    if not written:
        report.notes.append(f"质感曲线:{_NO_DATA_HINT}(还没有正文)")
        return

    rows: list[dict[str, Any]] = []
    for ch in written:
        text = ch.final_content or ""
        score = float(ai_flavor_report(text).score)
        rows.append({
            "chapter": ch.chapter_number,
            "score": round(score, 2),
            "chars": len(text.strip()),
        })
    report.flavor_curve = rows
    report.mean_flavor = round(sum(r["score"] for r in rows) / len(rows), 2)
    # 最差三章:AI 味最高的,给「哪几章该重写」一个确定性排序
    report.worst_flavor = sorted(rows, key=lambda r: -r["score"])[:3]


def _fill_tension(db: Session, project_id: int, report: HealthReport) -> None:
    """逐章张力曲线:该章所有场景卡 tension_level 的均值与峰值。

    「平章」判定:一张卡都没有的章不算平(那是没切场景),有卡但全章张力
    完全无起伏(peak == mean,即所有场景一个档)才算——这正是「文绉绉、
    像喝白水」在数据上的样子。
    """
    from app.db.models import Scene

    scenes = (
        db.query(Scene)
        .filter(Scene.project_id == project_id)
        .order_by(Scene.chapter_number, Scene.seq)
        .all()
    )
    if not scenes:
        report.notes.append(
            f"节奏曲线:{_NO_DATA_HINT}(该书未启用场景卡;开新书或重规划后可用)"
        )
        return

    by_chapter: dict[int, list[int]] = {}
    for s in scenes:
        by_chapter.setdefault(s.chapter_number, []).append(int(s.tension_level or 3))

    rows: list[dict[str, Any]] = []
    flat: list[int] = []
    for n in sorted(by_chapter):
        levels = by_chapter[n]
        mean = sum(levels) / len(levels)
        peak = max(levels)
        rows.append({
            "chapter": n,
            "scenes": len(levels),
            "mean": round(mean, 2),
            "peak": peak,
            # 起伏 = 峰值 - 均值,越大说明该章越「有高潮」
            "swing": round(peak - mean, 2),
        })
        if len(levels) >= 2 and peak == min(levels):
            flat.append(n)
    report.tension_curve = rows
    report.tension_flat_chapters = flat


def _fill_consistency(db: Session, project_id: int, report: HealthReport) -> None:
    """未解决的一致性问题:按类型分组 + 涉及的章号。

    chapter_issues 没有 project_id,只挂 chapter_id——所以先取本书的章 id 集,
    再一次查出 open 问题(避免逐章 N+1),章号从 id→章号映射回填。
    """
    from app.db.models import Chapter, ChapterIssue

    chapters = (
        db.query(Chapter.id, Chapter.chapter_number)
        .filter(Chapter.project_id == project_id)
        .all()
    )
    num_by_id = {cid: num for cid, num in chapters}
    if not num_by_id:
        report.notes.append("一致性:无正文章,无问题可查")
        return

    issues = (
        db.query(ChapterIssue)
        .filter(
            ChapterIssue.chapter_id.in_(list(num_by_id)),
            ChapterIssue.status == "open",
        )
        .all()
    )
    report.open_issues = len(issues)
    by_type: dict[str, int] = {}
    chapters_hit: set[int] = set()
    for i in issues:
        by_type[i.issue_type] = by_type.get(i.issue_type, 0) + 1
        num = num_by_id.get(i.chapter_id)
        if num:
            chapters_hit.add(int(num))
    report.issues_by_type = by_type
    report.issue_chapters = sorted(chapters_hit)
    if not issues:
        report.notes.append("一致性:未解决问题 0 条(或该书尚未跑过全书体检)")


def _fill_foreshadow(db: Session, project_id: int, report: HealthReport) -> None:
    """伏笔健康度:四态分布 + 逾期未收 + 长线债务比。"""
    from app.db.models import Foreshadowing

    rows = (
        db.query(Foreshadowing)
        .filter(Foreshadowing.project_id == project_id)
        .all()
    )
    report.foreshadow_total = len(rows)
    if not rows:
        report.notes.append("伏笔:暂无登记(伏笔调度未启用)")
        return

    by_status: dict[str, int] = {}
    overdue: list[dict[str, Any]] = []
    for f in rows:
        by_status[f.status] = by_status.get(f.status, 0) + 1
        # 逾期:已埋/已强化、有预期回收章、但还没回收
        if (
            f.status in ("planted", "reinforced")
            and f.expected_payoff_chapter
            and not f.payoff_chapter
        ):
            overdue.append({
                "content": f.description,
                "planted": f.chapter_planted,
                "expected": f.expected_payoff_chapter,
                "importance": f.importance,
            })
    report.foreshadow_by_status = by_status
    report.overdue = sorted(overdue, key=lambda o: o["expected"])
    unresolved = by_status.get("planted", 0) + by_status.get("reinforced", 0)
    report.debt_ratio = round(unresolved / len(rows), 3)


def _fill_premise(db: Session, project_id: int, report: HealthReport) -> None:
    """核心梗健康度(docs/19 M4):梗兑现账的确定性聚合。

    - ledger 有账的章:按序出曲线(✓/✗/强度),算连续未兑现与兑现占比;
    - 已写但无账的章如实列进 uncovered(抽取降级/老书未补标都会落这);
    - 没建梗卡:premise_defined=False,一句话说明,不装样子。
    """
    from app.db.models import Chapter, Premise
    from app.db.models.premise_ledger import PremiseLedger

    premise = (
        db.query(Premise)
        .filter(Premise.project_id == project_id, Premise.kind == "main")
        .first()
    )
    if premise is None or not (premise.high_concept or "").strip():
        report.notes.append("未建核心梗卡:「梗健康度」无从谈起(本书设置里可补建)")
        return
    report.premise_defined = True
    report.premise_high_concept = premise.high_concept
    report.premise_beats = [str(b) for b in (premise.beats or [])]

    written_numbers = [
        c.chapter_number
        for c in db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
        .all()
        if (c.final_content or "").strip()
    ]
    ledgers = {
        l.chapter_number: l
        for l in db.query(PremiseLedger)
        .filter(PremiseLedger.project_id == project_id)
        .order_by(PremiseLedger.chapter_number)
        .all()
    }

    streak = 0
    fulfilled = 0
    for n in written_numbers:
        row = ledgers.get(n)
        if row is None:
            report.premise_uncovered_chapters.append(n)
            streak += 1  # 无账也视为「看不见兑现」,与未兑现同权重示警
            continue
        if row.fulfilled:
            streak = 0
            fulfilled += 1
        else:
            streak += 1
        report.premise_ledger_curve.append({
            "chapter": n, "fulfilled": row.fulfilled, "beat": row.beat,
            "strength": row.strength, "note": row.note,
        })
        report.premise_max_streak = max(report.premise_max_streak, streak)
    report.premise_unfulfilled_streak = streak
    covered = len(report.premise_ledger_curve)
    report.premise_fulfilled_ratio = (round(fulfilled / covered, 2) if covered else None)
    if not report.premise_ledger_curve:
        report.notes.append("梗健康度:已写章节暂无对账账(章后抽取自动记账;老书可用「全书补标节拍」)")


def _fill_cost(db: Session, report: HealthReport) -> None:
    """token 用量(全库口径,不按项目——llm_usage 没有 project_id 字段)。

    这是本报告唯一的**近似值**:用量表不记项目归属,所以这里的数字是
    「这台机器上的总用量」。要每本书的精确成本需要给 llm_usage 加列,
    属于另一次改动;本模块宁可标注口径,也不假装精确。
    """
    from sqlalchemy import func

    from app.db.models import LlmUsage

    row = db.query(
        func.coalesce(func.sum(LlmUsage.prompt_tokens), 0),
        func.coalesce(func.sum(LlmUsage.completion_tokens), 0),
    ).first()
    report.prompt_tokens = int(row[0] or 0) if row else 0
    report.completion_tokens = int(row[1] or 0) if row else 0
    total = report.prompt_tokens + report.completion_tokens
    if total:
        report.tokens_per_chapter = (
            round(total / report.chapters_written) if report.chapters_written else 0
        )
        report.notes.append(
            "成本:token 用量是**本机全库口径**(用量表不记项目归属),"
            "按章均摊仅作参考"
        )
    else:
        report.notes.append(f"成本:{_NO_DATA_HINT}(尚未产生记账记录)")


# ---------------- 渲染 ----------------

def _curve_line(rows: list[dict[str, Any]], key: str, *, width: int = 10) -> str:
    """把一条逐章曲线画成等宽条形图(Markdown 里可读)。空数据返回空串。"""
    if not rows:
        return ""
    values = [float(r[key]) for r in rows]
    hi = max(values) or 1.0
    bars = "▁▂▃▄▅▆▇█"
    out = []
    for r in rows:
        v = float(r[key])
        idx = min(len(bars) - 1, int(v / hi * (len(bars) - 1))) if hi else 0
        out.append(bars[idx])
    return "".join(out)


def render_health(report: HealthReport, *, limit: int = 10) -> str:
    """体检报告 → Markdown(可下载、可贴官网)。"""
    lines: list[str] = [f"# 《{report.title or '未命名'}》成书体检报告", ""]

    # ① 体量
    lines.append("## 体量")
    lines.append("")
    lines.append(f"- 已成章 **{report.chapters_written}** / 蓝图 {report.chapters_planned} 章"
                 f"(完成度 {round(report.completion * 100)}%)")
    lines.append(f"- 总字数 **{report.total_words}**,平均每章 {report.avg_chapter_words} 字")
    lines.append("")

    # ② 质感
    lines.append("## 质感曲线(AI 味指数,越低越好)")
    lines.append("")
    if report.flavor_curve:
        lines.append(f"```\n{_curve_line(report.flavor_curve, 'score')}\n```")
        lines.append(f"全书均值 **{report.mean_flavor}**")
        if report.worst_flavor:
            worst = "、".join(
                f"第{r['chapter']}章({r['score']})" for r in report.worst_flavor
            )
            lines.append(f"最该复核:{worst}")
    else:
        lines.append("_暂无正文_")
    lines.append("")

    # ③ 节奏
    lines.append("## 节奏曲线(逐章张力,1 压抑 → 5 爆发)")
    lines.append("")
    if report.tension_curve:
        lines.append(f"```\n{_curve_line(report.tension_curve, 'mean')}\n```")
        if report.tension_flat_chapters:
            flat = "、".join(f"第{n}章" for n in report.tension_flat_chapters[:limit])
            lines.append(f"⚠ **无起伏章**:{flat}(全章所有场景同一张力档)")
        else:
            lines.append("每章都有起伏。")
    else:
        lines.append("_暂无场景卡数据_")
    lines.append("")

    # ④ 一致性
    lines.append("## 一致性")
    lines.append("")
    lines.append(f"- 未解决问题 **{report.open_issues}** 条")
    if report.issues_by_type:
        detail = "、".join(f"{k}×{v}" for k, v in report.issues_by_type.items())
        lines.append(f"- 按类型:{detail}")
    if report.issue_chapters:
        chs = "、".join(f"第{n}章" for n in report.issue_chapters[:limit])
        lines.append(f"- 涉及章节:{chs}")
    lines.append("")

    # ⑤ 伏笔
    lines.append("## 伏笔健康度")
    lines.append("")
    if report.foreshadow_total:
        lines.append(f"- 共 **{report.foreshadow_total}** 条,未回收债务比 **{round(report.debt_ratio * 100)}%**")
        if report.foreshadow_by_status:
            detail = "、".join(f"{k}×{v}" for k, v in report.foreshadow_by_status.items())
            lines.append(f"- 四态分布:{detail}")
        for o in report.overdue[:limit]:
            lines.append(f"  - ⏰ 逾期未收(预期第{o['expected']}章):{o['content']}")
    else:
        lines.append("_暂无登记伏笔_")
    lines.append("")

    # ⑤+' 核心梗健康度
    lines.append("## 核心梗健康度")
    lines.append("")
    if not report.premise_defined:
        lines.append("_未建核心梗卡_(本书设置里可补建)")
    elif not report.premise_ledger_curve:
        lines.append("_已写章节暂无对账账(章后抽取自动记账;老书可用「全书补标节拍」)_")
    else:
        lines.append(f"- 高概念:**{report.premise_high_concept}**")
        ratio = report.premise_fulfilled_ratio
        lines.append(
            f"- 有账章节兑现占比 **{round((ratio or 0) * 100)}%**,"
            f"最近连续未兑现 **{report.premise_unfulfilled_streak}** 章,"
            f"全书最长 **{report.premise_max_streak}** 章"
        )
        if report.premise_uncovered_chapters:
            chs = "、".join(f"第{n}章" for n in report.premise_uncovered_chapters[:limit])
            lines.append(f"- ⚠ 无对账账的已写章:{chs}")
        if report.premise_unfulfilled_streak >= 3:
            lines.append("⚠ **连续多章未兑现核心梗——这是文扑的前兆**,建议回读梗卡调整后续走向")
    lines.append("")

    # ⑥ 成本
    lines.append("## 成本")
    lines.append("")
    if report.prompt_tokens or report.completion_tokens:
        total = report.prompt_tokens + report.completion_tokens
        lines.append(f"- 累计 token {total}(输入 {report.prompt_tokens} / 输出 {report.completion_tokens})")
        lines.append(f"- 按章均摊约 {report.tokens_per_chapter} token")
    else:
        lines.append("_暂无记账数据_")
    lines.append("")

    if report.notes:
        lines.append("---")
        lines.append("")
        lines.append("> **口径说明**")
        for n in report.notes:
            lines.append(f"> - {n}")
        lines.append("")

    return "\n".join(lines)
