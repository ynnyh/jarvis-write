# app/engines/adapt_audit.py
# -*- coding: utf-8 -*-
"""改编质量验收(docs/15 §5.3):改编稿对原著的**保真度**,确定性算,零 LLM。

为什么需要它:改编此前**完全没有验收**——小说线有 7 项关卡(一致性/审校/去味/
字数守卫/契约…),而剧本与漫剧写了就落库,没有任何东西回答「改编丢没丢原著的
关键设定」。§5.1 把事实层喂给了改编(输入端),本模块是它的**出口端**:核对
喂进去的关键事实到底有没有在改编稿里落地。

三条指标(全部确定性,可进 CI):
  ① **保真度**(`fidelity`):原著 critical/major 事实在改编稿里的覆盖率。
     比对不看措辞看**关键词**——事实内容(如「左臂被剑贯穿」)切成字面短语,
     改编稿命中即算保住。这是**有偏保守的估计**:模型换同义说法会算作丢失
     (宁可虚报丢失让人复核,也不虚报保住骗人)。
  ② **取舍声明**(`adapt_note`):剧本改编的 `adapt_note` 声明了取舍;检查声明
     里出现的原著关键词是否仍在稿里——声明「删掉了 X」而 X 还在,或声明
     「保留了 Y」而 Y 没了,都是需要人看一眼的不一致。
  ③ **覆盖断点**(`gaps`):按章核对,哪几章的关键事实一条都没落地。

**advisory 语义(重要)**:本模块只产出报告,不设卡口、不改任何生成行为。
理由是事实抽取本身有噪声(见 app/engines/consistency/extractor.py),拿有噪
的输入去硬卡改编,会变成「误杀好改编」;而报告给人看不花钱,价值已经拿到。

边界:本模块是叶子——只依赖 db 模型 + `app.engines.adapt` 的事实层,不 import
api 层,也不 import drama/script 引擎(改编稿文本由调用方抽好传进来)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

# 一条事实至少要有这么长的「可用短语」才参与比对。太短的事实(如「重伤」)满
# 篇都是字面命中,算了也是噪声。
MIN_PHRASE_CHARS = 4

# 每条事实最多切出这么多候选短语(比对是 O(短语 × 稿长),上限防爆)
MAX_PHRASES_PER_FACT = 6

# 断言稿里「成段文字」时才值得比对:改编稿太短 = 还没写,不该报 0% 保真
MIN_ADAPT_CHARS = 120

# 中文标点 + 英文标点 + 空白:切分句/短语用。
# ASCII 那半必须也列上——LLM 与手写数据里半角逗号/句点比全角更常见,
# 漏了它们会让整句连成一整个「短语」,比对永远命中不了(实测踩过)。
_SPLIT = re.compile(
    r"[，,。\.、；;:：!?！?…—\-\s「」『』\"'“”‘’（）()《》\[\]【】/\\|]+"
)


@dataclass
class FactCheck:
    """一条事实的落地情况。"""

    fact_id: int
    content: str
    importance: str
    from_chapter: int
    kept: bool
    # 命中的短语(kept=True 时有值;便于人核对「是靠哪半句命中的」)
    matched_phrase: str = ""


@dataclass
class ChapterFidelity:
    chapter_number: int
    total: int
    kept: int

    @property
    def ratio(self) -> float:
        return (self.kept / self.total) if self.total else 1.0


@dataclass
class AdaptFidelity:
    """改编保真度总报。"""

    adapted_chars: int = 0
    facts_total: int = 0
    facts_kept: int = 0
    checks: list[FactCheck] = field(default_factory=list)
    chapters: list[ChapterFidelity] = field(default_factory=list)
    # 声明了取舍但出现不一致的地方(人话描述)
    note_issues: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.facts_kept / self.facts_total) if self.facts_total else 1.0

    @property
    def lost(self) -> list[FactCheck]:
        return [c for c in self.checks if not c.kept]

    @property
    def lost_critical(self) -> list[FactCheck]:
        return [c for c in self.lost if c.importance == "critical"]

    @property
    def gaps(self) -> list[int]:
        """整章一条事实都没落地的章号(覆盖断点)。"""
        return [c.chapter_number for c in self.chapters if c.total and not c.kept]


def _phrases_of(content: str) -> list[str]:
    """事实内容 → 候选比对短语(长句优先,太短的丢掉)。"""
    parts = [p.strip() for p in _SPLIT.split(content or "") if p.strip()]
    usable = [p for p in parts if len(p) >= MIN_PHRASE_CHARS]
    usable.sort(key=len, reverse=True)
    return usable[:MAX_PHRASES_PER_FACT]


def check_fact(content: str, adapted_text: str) -> tuple[bool, str]:
    """一条事实是否在改编稿里落地。返回 (是否命中, 命中短语)。

    判据取**任一候选短语字面出现在稿里**。保守起见不做同义/模糊匹配:
    虚报「保住了」比虚报「丢了」危害大得多(前者让人不去查,后者最多多看一眼)。
    """
    for ph in _phrases_of(content):
        if ph in adapted_text:
            return True, ph
    return False, ""


def audit_adaptation(
    db: Session,
    project_id: int,
    adapted_text: str,
    *,
    chapter_numbers: Iterable[int] | None = None,
    adapt_note: str = "",
    limit: int = 40,
) -> AdaptFidelity:
    """核一份改编稿对原著事实层的保真度。

    ``adapted_text`` 由调用方抽好(剧本正文 / 漫剧台词拼接)——本模块不认产物
    格式,只认「这是一段改编出来的文字」。``chapter_numbers`` 给定时只核这几章
    取材的事实(改编通常只覆盖部分章);不给则核全书。
    """
    from app.engines.adapt import chapter_facts

    text = adapted_text or ""
    report = AdaptFidelity(adapted_chars=len(text))

    if len(text.strip()) < MIN_ADAPT_CHARS:
        # 还没写出东西:不报 0%(那是「未评估」,不是「保真度差」)
        return report

    nums = sorted({int(n) for n in (chapter_numbers or [])}) or None
    if nums:
        facts = chapter_facts(db, project_id, nums, limit=limit)
    else:
        facts = _all_facts(db, project_id, limit=limit)

    report.facts_total = len(facts)
    by_chapter: dict[int, list[FactCheck]] = {}
    for f in facts:
        kept, phrase = check_fact(f.get("content") or "", text)
        check = FactCheck(
            fact_id=int(f.get("fact_id") or 0),
            content=str(f.get("content") or ""),
            importance=str(f.get("importance") or "major"),
            from_chapter=int(f.get("from_chapter") or 0),
            kept=kept,
            matched_phrase=phrase,
        )
        report.checks.append(check)
        if check.kept:
            report.facts_kept += 1
        by_chapter.setdefault(check.from_chapter, []).append(check)

    for ch in sorted(by_chapter):
        rows = by_chapter[ch]
        report.chapters.append(
            ChapterFidelity(
                chapter_number=ch,
                total=len(rows),
                kept=sum(1 for r in rows if r.kept),
            )
        )

    if adapt_note.strip():
        report.note_issues = _note_issues(adapt_note, text, report)
    return report


def _all_facts(db: Session, project_id: int, *, limit: int) -> list[dict]:
    """全书事实(不按章取材时用)。复用 BibleService 的退场过滤与排序口径。"""
    from app.db.models import Fact

    rows = (
        db.query(Fact)
        .filter(Fact.project_id == project_id)
        .all()
    )
    rank = {"critical": 0, "major": 1, "minor": 2}
    rows.sort(key=lambda f: (rank.get(f.importance, 1), f.valid_from or 0))
    return [
        {
            "fact_id": f.id,
            "content": (f.content or "")[:120],
            "importance": f.importance or "major",
            "from_chapter": f.valid_from or 0,
        }
        for f in rows[:limit]
    ]


def _note_issues(note: str, text: str, report: AdaptFidelity) -> list[str]:
    """核对取舍声明与稿件实际的一致性。

    只报**能确定性判定**的两种矛盾(不做语义理解,那需要 LLM,而这一层刻意
    不上 LLM——见模块 docstring 的 advisory 说明):
      · 声明「保留/强调」了某条在稿里找不到的事实 → 声明没落地;
      · 声明「删/略去」了某条却在稿里命中 → 改完忘了改声明,或删得不干净。
    声明句里出现的事实内容作关键词。
    """
    issues: list[str] = []
    for check in report.checks:
        # 用「最长候选短语」作关键词,不用 content[:N] 裸切——裸切会把标点
        # 切进中间(「林砚的左臂被剑贯穿,伤口」),声明里永远不会逐字出现,
        # 比对恒假。这是踩过的坑。
        phrases = _phrases_of(check.content)
        phrase = check.matched_phrase or (phrases[0] if phrases else "")
        if not phrase:
            continue
        mentioned = phrase in note
        if mentioned and not check.kept:
            issues.append(f"声明提到「{phrase}」,但改编稿里找不到")
        elif mentioned and check.kept and _is_deletion_claim(note, phrase):
            issues.append(f"声明说要删「{phrase}」,但改编稿里仍有")
    return issues[:10]


_DELETION_WORDS = ("删", "略去", "省略", "舍弃", "去掉", "舍去", "不写", "剔除")


def _is_deletion_claim(note: str, phrase: str) -> bool:
    """声明里那句提到 phrase 的话,是不是「删掉」的意思。"""
    for seg in re.split(r"[。;；\n]", note):
        if phrase in seg and any(w in seg for w in _DELETION_WORDS):
            return True
    return False


def render_fidelity(report: AdaptFidelity, *, limit: int = 8) -> str:
    """保真度报告 → 人话(空报告返回空串,不打印「100%」骗人)。"""
    if report.facts_total == 0 or report.adapted_chars < MIN_ADAPT_CHARS:
        return ""
    pct = round(report.ratio * 100)
    lines = [f"关键设定保真度:{pct}%({report.facts_kept}/{report.facts_total})"]
    if report.gaps:
        lines.append("整章未落地:" + "、".join(f"第{n}章" for n in report.gaps[:limit]))
    for c in report.lost_critical[:limit]:
        lines.append(f"  ❗未落地(关键):{c.content[:40]}")
    others = [c for c in report.lost if c.importance != "critical"]
    for c in others[: max(0, limit - len(report.lost_critical))]:
        lines.append(f"  · 未落地:{c.content[:40]}")
    for issue in report.note_issues[:limit]:
        lines.append(f"  ⚠ 取舍声明:{issue}")
    return "\n".join(lines)
