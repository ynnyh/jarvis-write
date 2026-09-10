# app/engines/consistency/fact_ledger.py
# -*- coding: utf-8 -*-
"""事实引用追踪与失效传播(§1.5)。

解决两个具体问题:

**① 改一条事实,没人知道哪些章引用了它。**
   以前要把全书正文扫一遍才知道;现在是一次索引查询(fact_usages)。
   消费日志有两条来源:检索层把事实喂进某场 prompt 时记一笔(retrieval);
   抽取时在新章正文里字面命中旧事实时记一笔(extract)。

**② 一条事实作废,依赖它的下游不会自动标记复核。**
   `invalidate_fact` 把 valid_until 往前拨,然后反查消费章,产出
   「受影响清单」交给级联引擎(setting_cascade)——后者本来就能扫全书
   受影响章,缺的正是「哪条事实影响哪几章」这个索引。

设计取舍:
- **消费日志只追加不删**:正文重写会让旧日志失真(某章不再引用它了),
  但删日志要重扫正文。折中:重写正文时该章的日志由 `forget_chapter` 清掉,
  下次生成重建——这一条是精确的,不贵(一个 DELETE)。
- **置信分层**:retrieval(模型确实看到了)> manual(作者标注)>
  extract(可能只是重述)。失效时按 source 分档提示复核力度,但**都提示**
  ——漏报比误报贵(作者看一眼就知道是不是真依赖)。
- **不做级联执行**:本模块只产报告,执行权在级联引擎与用户手里。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import Chapter, Entity, Fact, FactUsage, Scene

logger = logging.getLogger("jarvis-write.fact_ledger")

# 证据片段长度:够作者判断是不是真引用,又不至于把 prompt 撑大
_EVIDENCE_CHARS = 60
# extract 路的字面命中门槛:事实内容太短(如"重伤"两个字)会满地误命中
_MIN_MATCH_CHARS = 4
_MIN_MATCH_RATIO = 0.34

# source → 复核力度(retrieval 最强:模型确实吃到了;extract 最弱:可能只是重述)
_SOURCE_RANK = {"manual": 0, "retrieval": 1, "extract": 2}
_SOURCE_CN = {
    "manual": "人工标注", "retrieval": "检索注入", "extract": "抽取字面命中",
}


# ---------- 写入侧:消费日志 ----------

def record_usages(
    db: Session,
    project_id: int,
    fact_ids: list[int] | tuple[int, ...],
    chapter_number: int,
    *,
    scene_id: int | None = None,
    source: str = "retrieval",
    evidence: str = "",
) -> int:
    """把「本章(本场)用到了这几条事实」记进日志。幂等(同键累加 times)。

    返回实际写入/更新的行数。空 fact_ids 直接返回 0(不建行)。

    **循环内每次 add 后 flush**:查询看不到未 flush 的 pending 行。同一 Session
    连续调两次(如一场先记 retrieval、同章稍后又记 extract),若不 flush,
    第二次查询会把上一条已 add 未落库的行当成「不存在」再 add 一条,
    于是同一键出现两行(实测:一次生成里第 3 章第 1 场出现 3 行而非 2 行)。
    """
    ids = [int(i) for i in dict.fromkeys(fact_ids or [])]  # 去重且保序
    if not ids:
        return 0
    written = 0
    for fid in ids:
        row = (
            db.query(FactUsage)
            .filter(
                FactUsage.fact_id == fid,
                FactUsage.chapter_number == chapter_number,
                FactUsage.scene_id == scene_id,
                FactUsage.source == source,
            )
            .first()
        )
        if row is None:
            db.add(FactUsage(
                project_id=project_id,
                fact_id=fid,
                chapter_number=chapter_number,
                scene_id=scene_id,
                source=source,
                times=1,
                evidence=evidence[:_EVIDENCE_CHARS],
            ))
            # 立刻落库,让本轮后续查询(含下一次调用)能看到它
            db.flush()
        else:
            row.times = int(row.times or 0) + 1
            if evidence and not row.evidence:
                row.evidence = evidence[:_EVIDENCE_CHARS]
        written += 1
    return written


def record_retrieval(
    db: Session,
    project_id: int,
    scene: Scene,
    contexts: list[dict],
) -> int:
    """检索结果列表 → 消费日志(生成路径的调用点)。

    兼容两种形状:``SceneContext.facts``(键名 ``id``)与外部直接给的
    ``{"fact_id": ...}``。两者都认,免得调用点为了对齐键名再做一次搬运。
    """
    def _pick(c: dict) -> int | None:
        for key in ("id", "fact_id"):
            v = c.get(key)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
        return None

    ids = [i for i in (_pick(c) for c in (contexts or [])) if i is not None]
    n = record_usages(
        db, project_id, ids,
        int(getattr(scene, "chapter_number", 0) or 0),
        scene_id=getattr(scene, "id", None),
        source="retrieval",
    )
    if n:
        db.flush()
        logger.debug("第 %s 章第 %s 场:记录 %d 条事实消费",
                     getattr(scene, "chapter_number", "?"), getattr(scene, "seq", "?"), n)
    return n


def record_extract_hits(
    db: Session,
    project_id: int,
    chapter_number: int,
    chapter_text: str,
    *,
    facts: list[Fact] | None = None,
) -> int:
    """抽取路的字面命中:新章正文里出现了旧事实的内容 → 记一笔。

    这是**弱信号**(正文可能只是重述而非依赖),故 source="extract",
    失效传播时排最后,只在没有强信号时才作为补充列出。

    门槛两条,防短事实满地误命中:
      - 长度 ≥ _MIN_MATCH_CHARS;
      - 命中片段占事实内容的比例 ≥ _MIN_MATCH_RATIO(取事实内容的
        最长可匹配前缀段,避开通用的连接词)。
    """
    text = chapter_text or ""
    if not text.strip():
        return 0
    if facts is None:
        facts = (
            db.query(Fact)
            .filter(
                Fact.project_id == project_id,
                Fact.valid_from < chapter_number,
            )
            .all()
        )
    hits: list[tuple[int, str]] = []
    for f in facts:
        probe = _probe_phrase(f.content)
        if probe and probe in text:
            hits.append((f.id, probe))
    if not hits:
        return 0
    for fid, probe in hits:
        record_usages(
            db, project_id, [fid], chapter_number,
            source="extract", evidence=probe,
        )
    db.flush()
    logger.debug("第 %d 章抽取路:字面命中 %d 条事实", chapter_number, len(hits))
    return len(hits)


_PUNCT = re.compile(r"[，。、；:：!?！?…—\-\s「」『』\"'（）()《》\[\]]+")


def _probe_phrase(content: str) -> str:
    """从事实内容里取一段「够长、够独特」的探针短语。

    事实内容常是「林砚的左臂被剑贯穿,伤口已结痂」这种带标点的句子;
    整句匹配几乎不可能命中,故取最长的一个分句。分句全部过短 → 空串
    (宁可漏报,不要用「受伤」这种词造成满地误报)。
    """
    if not content:
        return ""
    parts = [p.strip() for p in _PUNCT.split(content) if p.strip()]
    if not parts:
        return ""
    probe = max(parts, key=len)
    if len(probe) < _MIN_MATCH_CHARS:
        return ""
    if len(probe) < len(content.strip()) * _MIN_MATCH_RATIO:
        return ""
    return probe


def forget_chapter(db: Session, project_id: int, chapter_number: int) -> int:
    """清掉某章的消费日志(正文重写时调用:旧引用已随正文失效)。

    为什么能精确删而不重扫:日志是**按章**记的,章正文变了,该章的
    消费记录整体作废;其他章的记录不受影响。下次生成该章时重建。
    """
    n = (
        db.query(FactUsage)
        .filter(
            FactUsage.project_id == project_id,
            FactUsage.chapter_number == chapter_number,
        )
        .delete(synchronize_session=False)
    )
    if n:
        db.flush()
        logger.debug("第 %d 章正文重写:清掉 %d 条旧消费日志", chapter_number, n)
    return int(n or 0)


# ---------- 读取侧:引用查询与失效传播 ----------

@dataclass
class UsageRow:
    chapter_number: int
    scene_id: int | None
    source: str
    times: int
    evidence: str

    @property
    def source_cn(self) -> str:
        return _SOURCE_CN.get(self.source, self.source)


def usages_of(db: Session, project_id: int, fact_id: int) -> list[UsageRow]:
    """一条事实被哪些章/场消费过(按章号、来源可信度排序)。"""
    rows = (
        db.query(FactUsage)
        .filter(
            FactUsage.project_id == project_id,
            FactUsage.fact_id == fact_id,
        )
        .all()
    )
    out = [
        UsageRow(
            chapter_number=int(r.chapter_number),
            scene_id=r.scene_id,
            source=r.source or "extract",
            times=int(r.times or 1),
            evidence=r.evidence or "",
        )
        for r in rows
    ]
    out.sort(key=lambda u: (u.chapter_number, _SOURCE_RANK.get(u.source, 9)))
    return out


@dataclass
class Writeback:
    """一条事实的「回写面」:它被写进了哪些章,以及是否还在生效。"""

    fact_id: int
    content: str
    importance: str
    valid_from: int
    valid_until: int | None
    sources: list[str] = field(default_factory=list)

    @property
    def open_ended(self) -> bool:
        return self.valid_until is None


def writeback_map(
    db: Session, project_id: int, *, chapter_number: int
) -> list[Writeback]:
    """给定章号,列出「此刻生效、且这一章引用了」的事实(反向查询)。

    体检报告用:这一章是在哪些事实的支撑下写出来的。
    """
    rows = (
        db.query(FactUsage)
        .filter(
            FactUsage.project_id == project_id,
            FactUsage.chapter_number == chapter_number,
        )
        .all()
    )
    by_fact: dict[int, set[str]] = {}
    for r in rows:
        by_fact.setdefault(int(r.fact_id), set()).add(r.source or "extract")
    if not by_fact:
        return []
    facts = (
        db.query(Fact)
        .filter(Fact.project_id == project_id, Fact.id.in_(list(by_fact)))
        .all()
    )
    out = [
        Writeback(
            fact_id=f.id,
            content=f.content,
            importance=f.importance or "major",
            valid_from=int(f.valid_from or 0),
            valid_until=f.valid_until,
            sources=sorted(by_fact.get(f.id, set()), key=lambda s: _SOURCE_RANK.get(s, 9)),
        )
        for f in facts
    ]
    out.sort(key=lambda w: (_RANK.get(w.importance, 1), w.valid_from))
    return out


_RANK = {"critical": 0, "major": 1, "minor": 2}


# ---------- 失效传播 ----------

@dataclass
class AffectedChapter:
    chapter_number: int
    scene_ids: list[int]
    strongest_source: str
    evidence: str
    is_written: bool  # 该章已有正文 → 真要复核;没写 → 生成时自然会用新事实

    @property
    def strongest_source_cn(self) -> str:
        return _SOURCE_CN.get(self.strongest_source, self.strongest_source)


@dataclass
class InvalidationReport:
    """失效传播报告:一条事实作废后,哪些章要复核。"""

    fact_id: int
    content: str
    old_valid_until: int | None
    new_valid_until: int | None
    invalidated_from: int
    affected: list[AffectedChapter] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def written_affected(self) -> list[AffectedChapter]:
        """已写正文、确实需要复核的章(未写的章不用管)。"""
        return [a for a in self.affected if a.is_written]

    @property
    def critical_count(self) -> int:
        return sum(1 for a in self.written_affected if a.strongest_source == "retrieval")


def invalidate_fact(
    db: Session,
    project_id: int,
    fact_id: int,
    *,
    valid_until: int,
    dry_run: bool = False,
) -> InvalidationReport:
    """把一条事实在 valid_until 章失效,并产出受影响章清单。

    dry_run=True 只看影响不落库(UI 上「先看看会影响什么」)。
    本函数不删消费日志:日志记录的是历史事实(第 8 章确实用过它),
    复核完成后由调用方决定是否 forget_chapter。
    """
    fact = (
        db.query(Fact)
        .filter(Fact.project_id == project_id, Fact.id == fact_id)
        .first()
    )
    if fact is None:
        raise ValueError(f"事实 {fact_id} 不存在")

    old_until = fact.valid_until
    report = InvalidationReport(
        fact_id=fact.id,
        content=fact.content,
        old_valid_until=old_until,
        new_valid_until=valid_until,
        invalidated_from=valid_until,
    )

    usages = usages_of(db, project_id, fact_id)
    # 只有「失效点之后仍写了它」的章才受影响;此前用过它是正常的(那时还有效)
    relevant = [u for u in usages if u.chapter_number >= valid_until]
    if not relevant:
        report.notes.append(
            f"没有已写章节在失效点(第 {valid_until} 章)之后引用过它,无需复核"
        )
    else:
        written = {
            int(c.chapter_number)
            for c in db.query(Chapter.chapter_number)
            .filter(
                Chapter.project_id == project_id,
                Chapter.chapter_number.in_({u.chapter_number for u in relevant}),
                Chapter.final_content != "",
            )
            .all()
        }
        grouped: dict[int, AffectedChapter] = {}
        for u in relevant:
            cur = grouped.get(u.chapter_number)
            best = min(
                (cur.strongest_source, u.source) if cur else (u.source, u.source),
                key=lambda s: _SOURCE_RANK.get(s, 9),
            )
            if cur is None:
                grouped[u.chapter_number] = AffectedChapter(
                    chapter_number=u.chapter_number,
                    scene_ids=[u.scene_id] if u.scene_id else [],
                    strongest_source=u.source,
                    evidence=u.evidence,
                    is_written=u.chapter_number in written,
                )
            else:
                if u.scene_id and u.scene_id not in cur.scene_ids:
                    cur.scene_ids.append(u.scene_id)
                cur.strongest_source = best
                if not cur.evidence and u.evidence:
                    cur.evidence = u.evidence
        report.affected = sorted(grouped.values(), key=lambda a: a.chapter_number)
        n_written = len(report.written_affected)
        if n_written:
            report.notes.append(
                f"共 {n_written} 章已写正文且引用了它,建议按此清单复核"
                "(强信号优先:检索注入 > 抽取字面命中)"
            )
        else:
            report.notes.append(
                "引用它的章都还没写正文,生成时会自然读到新事实,无需人工复核"
            )

    if not dry_run:
        fact.valid_until = valid_until
        db.flush()
        logger.info(
            "事实 %d 失效传播:valid_until=%s,受影响已写章 %d 个",
            fact_id, valid_until, len(report.written_affected),
        )
    return report


def render_invalidation_report(report: InvalidationReport, *, limit: int = 20) -> str:
    """失效报告 → 给用户/级联引擎看的文本。"""
    head = (
        f"【事实失效复核】{report.content}\n"
        f"- 失效点:第 {report.invalidated_from} 章起"
        f"(原 valid_until={report.old_valid_until if report.old_valid_until is not None else '未设'})"
    )
    lines = [head]
    if not report.affected:
        lines.append("- 无引用记录")
    else:
        lines.append(f"- 引用过它的章({len(report.affected)} 个,按章号):")
        for a in report.affected[:limit]:
            tag = "" if a.is_written else "(未写正文,忽略)"
            where = f"第{a.scene_ids[0]}场" if a.scene_ids else ""
            ev = f" | 依据:{a.evidence}" if a.evidence else ""
            lines.append(
                f"  · 第{a.chapter_number}章{where} [{a.strongest_source_cn}]{tag}{ev}"
            )
        if len(report.affected) > limit:
            lines.append(f"  …另有 {len(report.affected) - limit} 章")
    lines.extend(f"- {n}" for n in report.notes)
    return "\n".join(lines)
