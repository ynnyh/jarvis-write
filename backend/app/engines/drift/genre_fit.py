# app/engines/drift/genre_fit.py
# -*- coding: utf-8 -*-
"""题材贴合检测器:纯正则 / 统计,零 LLM(仿 polish/ai_flavor 的形态)。

两问:
1. 负向——文本里有没有「越界」的非现实元素?(禁忌命中,高召回预筛)
2. 正向——用户点名「必须有」的看点落地了没?(必须有覆盖,粗信号)

设计与 ai_flavor 对齐:先出一份廉价、确定性的报告(regex + 统计),再由
self_heal 决定要不要花 LLM 去精筛/重写。所以本模块只给「线索」,不下「判决」:
- 禁忌命中是「疑似越界」,是否真越界交给 self_heal.confirm_forbidden 的 LLM-judge;
- 必须有缺失是「初稿未见字面」,是否真缺失同样宜由重写环节兜底(粗信号防漏,不误杀)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.engines.drift.contracts import effective_patterns

# 每个禁忌 label 最多记几处命中(防某个宽正则在长文里刷屏)。
_MAX_HITS_PER_LABEL = 5
# 命中片段左右各截多少字做上下文(给 LLM-judge / 前端展示看语境)。
_SNIPPET_PAD = 12


@dataclass(frozen=True)
class GenreHit:
    """一处疑似越界命中。"""

    label: str        # 禁忌元素名(如「金手指式觉醒」)
    phrase: str       # 实际命中的字面
    snippet: str      # 命中处上下文(便于人/LLM 判断是不是误报)
    start: int        # 命中起始下标


@dataclass
class GenreFitReport:
    """题材贴合报告:禁忌命中(负向)+ 必须有覆盖(正向)。"""

    mode: str = ""
    total_chars: int = 0
    forbidden: list[GenreHit] = field(default_factory=list)
    taste_score: int = 100                       # 必须有覆盖度 0-100(无 must 时 100)
    taste_notes: list[str] = field(default_factory=list)  # 未体现的「必须有」提示

    def has_forbidden(self) -> bool:
        return bool(self.forbidden)

    def forbidden_labels(self) -> list[str]:
        """去重后的越界元素名(判过的候选,喂给 LLM-judge)。顺序稳定。"""
        seen: dict[str, None] = {}
        for h in self.forbidden:
            seen.setdefault(h.label, None)
        return list(seen.keys())

    def is_clean(self) -> bool:
        """无疑似越界且必须有全覆盖。"""
        return not self.forbidden and not self.taste_notes

    def summary(self) -> str:
        parts = []
        if self.forbidden:
            parts.append(f"疑似越界 {len(self.forbidden)} 处:" + "、".join(self.forbidden_labels()))
        else:
            parts.append("无疑似越界")
        if self.taste_notes:
            parts.append(f"必须有未体现 {len(self.taste_notes)} 项")
        parts.append(f"味道覆盖 {self.taste_score}")
        return "；".join(parts)

    def advice_block(self) -> str:
        """给定向重写用的整改清单;全清则空串。"""
        lines: list[str] = []
        if self.forbidden:
            lines.append(
                "【疑似越界,须删改(除非用户明确要求,否则不得出现)】:"
                + "、".join(self.forbidden_labels())
            )
        for note in self.taste_notes:
            lines.append("【必须有,须补】:" + note)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_chars": self.total_chars,
            "has_forbidden": self.has_forbidden(),
            "forbidden_labels": self.forbidden_labels(),
            "forbidden": [
                {"label": h.label, "phrase": h.phrase, "snippet": h.snippet, "start": h.start}
                for h in self.forbidden
            ],
            "taste_score": self.taste_score,
            "taste_notes": list(self.taste_notes),
        }


def genre_fit_report(
    text: str,
    mode: str = "",
    must: tuple[str, ...] = (),
    must_not: tuple[str, ...] = (),
) -> GenreFitReport:
    """出一份题材贴合报告。

    - mode:题材模式,决定模式级禁忌是否生效(见 contracts.effective_patterns)。
    - must:用户点名「必须有」——双重身份:① 作为 opt_in,让同名禁忌放行(明确要=不算越界);
            ② 作为正向覆盖检查项(初稿里有没有体现)。
    - must_not:用户自定义「绝不能有」,按字面叠加进禁忌集。
    """
    text = text or ""
    report = GenreFitReport(mode=(mode or "").strip(), total_chars=len(text))

    # 负向:禁忌预筛(高召回;真伪交给 self_heal 的 LLM-judge)
    for label, pat in effective_patterns(mode, extra_must_not=tuple(must_not), opt_in=tuple(must)):
        count = 0
        for m in pat.finditer(text):
            if count >= _MAX_HITS_PER_LABEL:
                break
            s, e = m.start(), m.end()
            snippet = text[max(0, s - _SNIPPET_PAD): e + _SNIPPET_PAD].replace("\n", " ")
            report.forbidden.append(
                GenreHit(label=label, phrase=m.group(0), snippet=snippet, start=s)
            )
            count += 1

    # 正向:必须有覆盖(粗信号——字面未见即提示,不误杀,只提醒补)
    reqs = [m.strip() for m in must if m and m.strip()]
    if reqs:
        missing = [m for m in reqs if m not in text]
        report.taste_score = round((len(reqs) - len(missing)) / len(reqs) * 100)
        report.taste_notes = [f"必须有『{m}』但初稿未体现" for m in missing]

    return report
