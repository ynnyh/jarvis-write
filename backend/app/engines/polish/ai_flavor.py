# app/engines/polish/ai_flavor.py
# -*- coding: utf-8 -*-
"""AI 味量化检测:分类分权重规则库 + 句式统计指标,纯规则统计,不调 LLM。

检测结构(借鉴 humanize 九杠杆 / ai-flavor-remover 先诊断后治疗 / inkos 禁词表):
- 8 类规则库,每类独立计分、独立权重(网文"万能神态套话"权重最高)
- 4 个统计指标:句长 burstiness、节拍器句组、段落结构(段长均一+段尾总结句)、长重复
- 现代检测抓的是 RLHF 腔调模式,不是统计指纹:本指数只作"润色前后相对进步"
  度量,不承诺过任何 AI 检测器。

产出报告:score(/千字,与 UI 徽章兼容)+ summary + categories(分类得分)
+ hits(命中明细:类别/命中句原文/位置,供润色闭环定点改写)+ metrics(统计指标)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from statistics import mean, pstdev

# =============== 8 类规则库(类别, 权重, [(规则名, 正则)]) ===============
# 权重:网文神态套话最高(2.0);比喻连接词最轻(0.6,白描里少量可用)

_PHRASE = r"(?:{})"  # 短语表转义后或连


def _phrases(*words: str) -> str:
    return _PHRASE.format("|".join(re.escape(w) for w in words))


_RULES: list[tuple[str, float, list[tuple[str, str]]]] = [
    ("万能神态套话", 2.0, [
        (_phrases(
            "眼中闪过一丝", "眼底闪过", "眸光一沉", "嘴角勾起", "微微上扬",
            "勾起一抹弧度", "邪魅一笑", "面色铁青", "不禁", "不由得",
            "下意识地", "空气仿佛凝固", "时间仿佛静止", "命运的齿轮开始转动",
        ), None),
    ]),
    ("稳妥表达癖", 1.2, [
        (_phrases(
            "沉默片刻", "微微一笑", "轻声说道", "轻声说", "缓缓开口",
            "淡淡地道", "淡淡地说", "心中暗道",
        ), None),
    ]),
    ("总结过渡腔", 1.2, [
        (_phrases(
            "综上所述", "总而言之", "总的来说", "值得注意的是", "不难发现",
            "由此可见", "毫无疑问", "不可否认", "与此同时",
        ), None),
        (r"在这个[^。,,]{0,20}的世界里", "在这个……的世界里"),
    ]),
    ("说教报告腔", 1.0, [
        (_phrases("这个故事告诉我们", "可想而知", "换言之", "值得一提", "不得不说"), None),
    ]),
    ("逻辑连接癖", 0.8, [
        (r"(?:^|[。!?!?\n])\s*(?:首先|其次|再次|最后)[,,、]", "首先/其次/最后"),
        (r"一方面[^。]{0,30}另一方面", "一方面/另一方面"),
        (r"不仅[^。]{0,30}而且", "不仅……而且"),
        (r"虽然[^。]{0,30}但是", "虽然……但是"),
    ]),
    ("造句定式", 1.0, [
        (r"不是[^。,,;;]{1,20}[,,]?\s*而是", "不是A而是B"),
        (r"与其说", "与其说不如说"),
        (r"像[^。,,]{1,15}[,,]\s*又像", "像X又像Y"),
        (r"某种(意义|程度)上", "某种意义上"),
        (r"是[^。,,;;]{2,12}[,,]\s*是[^。,,;;]{2,12}[,,]\s*是", "排比堆砌"),
    ]),
    ("情绪标签直喊", 1.0, [
        (r"(感到|觉得|心中|心里|内心)[^。]{0,6}"
         r"(绝望|愤怒|悲伤|喜悦|恐惧|无比|难以言喻|五味杂陈|百感交集|复杂)",
         "情绪标签直喊"),
    ]),
    ("比喻连接词癖", 0.6, [
        (_phrases("仿佛", "宛如", "彷佛", "好似", "恍若"), None),
    ]),
]

# 段尾总结句词表(段落结构指标用):总结过渡腔 + 说教腔的收尾高频词
_TAIL_SUMMARY = re.compile(
    r"综上所述|总而言之|总的来说|由此可见|毫无疑问|不可否认|可想而知|"
    r"换言之|这个故事告诉我们|值得一提"
)

# =============== 统计指标阈值 ===============
_SENT_SPLIT = re.compile(r"[^。!?!?;;…\n]+[。!?!?;;…]*")
_MIN_SENTS = 8          # 句数太少不算 burstiness(短片段波动大,易误伤)
_BURSTINESS_RED = 0.4   # σ/μ 低于此标红(人类参考 0.6-1.2,AI 0.2-0.4)
_METRONOME_TOL = 5      # 连续 3 句长度差 <5 字算一组节拍器
_PARA_CV_RED = 0.25     # 段长变异系数低于此算"段落均一"
_REPEAT_MIN = 50        # 连续重复片段最短长度
_REPEAT_STEP = 100      # 重复扫描步长
_REPEAT_MAX_CHECKS = 200  # 扫描窗口上限(防爆)
_HIT_CAP_PER_CATEGORY = 20  # 每类命中明细上限


@dataclass
class FlavorHit:
    """单条命中明细:给润色闭环定点改写用。"""

    category: str   # 规则类别
    phrase: str     # 命中的套话/规则名
    sentence: str   # 命中句原文
    start: int      # 命中处在原文中的字符位置


@dataclass
class FlavorReport:
    total_chars: int
    score: float = 0.0  # 每千字加权命中数 + 统计指标 flat 罚分,越低越好
    # 分类得分:{类别: {"count": 命中数, "weight": 权重, "score": 加权分}}
    categories: dict[str, dict] = field(default_factory=dict)
    hits: list[FlavorHit] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        parts: list[str] = []
        if self.categories:
            top = sorted(
                self.categories.items(), key=lambda kv: -kv[1]["score"]
            )[:3]
            parts.append("、".join(f"{k}×{v['count']}" for k, v in top))
        if self.metrics.get("burstiness_flag"):
            parts.append("句长过于均匀")
        if self.metrics.get("metronome_groups"):
            parts.append(f"节拍器句组×{self.metrics['metronome_groups']}")
        if self.metrics.get("tail_summary_count"):
            parts.append(f"段尾总结句×{self.metrics['tail_summary_count']}")
        if self.metrics.get("repeats"):
            parts.append(f"长重复×{len(self.metrics['repeats'])}")
        if not parts:
            return f"AI 味指数 {self.score:.1f}/千字(未检出明显 AI 腔)"
        return f"AI 味指数 {self.score:.1f}/千字({';'.join(parts)})"

    def to_dict(self, max_hits: int = 20) -> dict:
        """JSON 友好结构;hits 截断防爆(整章命中可能很多)。"""
        return {
            "score": self.score,
            "summary": self.summary(),
            "total_chars": self.total_chars,
            "categories": self.categories,
            "hits": [vars(h) for h in self.hits[:max_hits]],
            "metrics": self.metrics,
        }


# =============== 内部:句子/段落切分 ===============

def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """按中文标点切句,返回 (start, end) 区间列表。"""
    return [(m.start(), m.end()) for m in _SENT_SPLIT.finditer(text) if m.group().strip()]


def _find_sentence(spans: list[tuple[int, int]], text: str, pos: int) -> str:
    for s, e in spans:
        if s <= pos < e:
            return text[s:e].strip()
    return text[max(0, pos - 20):pos + 20].strip()


# =============== 内部:4 个统计指标 ===============

def _burstiness(lengths: list[int]) -> tuple[float | None, bool]:
    """句长 burstiness:σ/μ。<0.4 标红(节拍器式均匀是 AI 特征)。"""
    if len(lengths) < _MIN_SENTS:
        return None, False
    cv = pstdev(lengths) / mean(lengths) if mean(lengths) else 0.0
    return round(cv, 3), cv < _BURSTINESS_RED


def _metronome_groups(lengths: list[int]) -> int:
    """节拍器段:连续 3 句长度差 <5 字的组数(命中后跳过该组,不重叠计)。"""
    groups, i = 0, 0
    while i + 2 < len(lengths):
        trio = lengths[i:i + 3]
        if max(trio) - min(trio) < _METRONOME_TOL:
            groups += 1
            i += 3
        else:
            i += 1
    return groups


def _paragraph_metrics(text: str) -> dict:
    """段落结构:段长变异系数过低 + 段尾总结句计数。"""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    result: dict = {"para_count": len(paras), "para_cv": None,
                    "para_uniform": False, "tail_summary_count": 0}
    if len(paras) >= 3:
        lens = [len(p) for p in paras]
        cv = pstdev(lens) / mean(lens) if mean(lens) else 0.0
        result["para_cv"] = round(cv, 3)
        result["para_uniform"] = cv < _PARA_CV_RED
    for p in paras:
        tail = _SENT_SPLIT.findall(p)
        if tail and _TAIL_SUMMARY.search(tail[-1]):
            result["tail_summary_count"] += 1
    return result


def _find_repeats(text: str) -> list[dict]:
    """长重复:difflib 找 50 字以上连续重复片段(窗口数 capped 防爆)。"""
    repeats: list[dict] = []
    n = len(text)
    positions = range(0, max(n - _REPEAT_MIN, 0), _REPEAT_STEP)
    for i in list(positions)[:_REPEAT_MAX_CHECKS]:
        window = text[i:i + _REPEAT_MIN]
        rest = text[i + _REPEAT_MIN:]
        m = SequenceMatcher(None, window, rest, autojunk=False).find_longest_match()
        if m.size >= _REPEAT_MIN:
            # 命中后向后顺延,拿到完整重复片段
            k = m.size
            while i + k < n and i + _REPEAT_MIN + m.b + k < n \
                    and text[i + k] == text[i + _REPEAT_MIN + m.b + k]:
                k += 1
            frag = text[i:i + k]
            if not any(r["text"] == frag for r in repeats):
                repeats.append({"start": i, "length": k, "text": frag[:80]})
    return repeats


# =============== 主入口 ===============

def ai_flavor_report(text: str) -> FlavorReport:
    """统计文本的 AI 腔特征。score = 每千字加权命中数(含统计指标罚分)。"""
    spans = _sentence_spans(text)
    hits: list[FlavorHit] = []
    categories: dict[str, dict] = {}
    weighted = 0.0

    for name, weight, rules in _RULES:
        count = 0
        for pattern, label in rules:
            for m in re.finditer(pattern, text):
                count += 1
                if count <= _HIT_CAP_PER_CATEGORY:
                    hits.append(FlavorHit(
                        category=name,
                        phrase=label or m.group(),
                        sentence=_find_sentence(spans, text, m.start()),
                        start=m.start(),
                    ))
        if count:
            categories[name] = {
                "count": count,
                "weight": weight,
                "score": round(count * weight, 2),
            }
            weighted += count * weight

    # ---- 统计指标 ----
    sent_lens = [e - s for s, e in spans]
    cv, burst_flag = _burstiness(sent_lens)
    metronome = _metronome_groups(sent_lens)
    para = _paragraph_metrics(text)
    repeats = _find_repeats(text)
    metrics = {
        "sentence_count": len(sent_lens),
        "burstiness": cv,
        "burstiness_flag": burst_flag,
        "metronome_groups": metronome,
        **para,
        "repeats": repeats,
    }

    # 指标罚分(flat 加分,不随字数折算:避免短文本被固定罚分放大)
    penalty = (3.0 if burst_flag else 0.0) \
        + min(metronome, 5) \
        + min(para["tail_summary_count"], 3) \
        + (1.0 if para["para_uniform"] else 0.0) \
        + min(len(repeats), 3)

    total = max(len(text), 1)
    score = weighted / total * 1000 + penalty
    return FlavorReport(
        total_chars=len(text),
        score=round(score, 2),
        categories=categories,
        hits=hits,
        metrics=metrics,
    )
