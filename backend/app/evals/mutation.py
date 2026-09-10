# app/evals/mutation.py
# -*- coding: utf-8 -*-
"""判别力轨(docs/15 §7.1 轨 C):故意写坏正文,验证指标**确实会掉**。

这一轨回答的是一个此前从没人问过的问题:**「这套评测到底拦不拦得住退化?」**

`thresholds.py` 的门槛是人为选的(基线 4.1 → 放行 6.0)。但「门槛数值」和
「门槛有没有判别力」是两件事:如果 AI 味算法对真正的 AI 味不敏感,门槛设得再
严也只是个摆设——PR 把 prompt 改烂,CI 照样绿。**没做过 mutation test,门槛
本身也是未经验证的。**

做法(确定性,零 LLM):拿一段**正常的人写正文**作底,按几种「典型退化」分别
注入,再算指标。每种退化都必须被至少一项确定性指标检出:

  · `repetitive` —— 复读:把同一句反复堆(模型的典型病症)。
  · `ai_flavor` —— AI 味:整齐排比 + 总结句 + 「不仅仅是…而是…」套话。
  · `truncated` —— 篇幅崩:只写目标的一小部分(字数守卫失效)。
  · `runaway` —— 刹不住:远超目标(压缩失效)。

判据是**相对**的(退化样本比正常样本差),不写死绝对值——绝对值随模型/语言漂移,
相对关系才是「这套尺子有没有用」的稳定证据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.evals.deterministic import DeterministicRun, score_texts

# 一段基准正文:正常人写的小说片段(无明显 AI 味、无复读、篇幅达标)。
# 刻意用**具体动作 + 感官细节**,而不是「他感到一种复杂的情绪」这类套话——
# 后者本身就有 AI 味,会污染基准。
BASELINE_TEXT = (
    "雪停了。沈砚把刀从鞘里抽出来,刀身上一层薄霜。他用拇指抹了一下,"
    "霜化成水,顺着刀脊淌到指尖。\n"
    "庙门被风撞开一条缝。他没有回头,只把手里的干粮掰成两半,一半塞进嘴里,"
    "嚼得很慢。\n"
    "外面有马蹄声。三骑。他数得出来,因为蹄声落在同一条冻土上,间隔一样。"
    "他把刀横放在膝上,掌心朝上,等那声音停下来。\n"
    "停在了门外。\n"
    "一个声音说:「沈砚。」只叫了名字,没有下文。\n"
    "「在。」他说。\n"
)

# ---- 退化注入器:每个都接收基准文本,返回「被写坏」的文本 ----


def mutate_repetitive(text: str) -> str:
    """复读:同一句反复出现(模型最常见病症)。"""
    line = "他握紧了刀,心里升起一股复杂的情绪。"
    return (text + "\n" + "\n".join([line] * 12) + "\n")


def mutate_ai_flavor(text: str) -> str:
    """AI 味:整齐排比 + 抽象总结 + 「不仅仅是…而是…」套话 + 点题说教。"""
    return text + "\n".join([
        "雪不仅仅是雪,更是命运无声的隐喻。",
        "他不仅是一个刀客,更是一个被过往禁锢的灵魂。",
        "在这一刻,他感受到了一种前所未有的力量,那是成长的滋味。",
        "他学会了接纳,也学会了放下。他知道,一切都会好起来的。",
        "这不仅仅是一场战斗,更是一次蜕变。",
        "风是冷的,心是热的,而未来是值得期待的。",
    ]) + "\n"


def mutate_truncated(text: str) -> str:
    """篇幅崩:只留下开头一小段(字数守卫失效/生成被截断未发现)。"""
    return text[: max(1, len(text) // 6)]


def mutate_runaway(text: str) -> str:
    """刹不住:把正文灌水放大到数倍(压缩失效)。"""
    return text * 6


@dataclass
class MutationCase:
    """一种退化 + 它必须被检出的证据。"""

    name: str
    describe: str
    mutate: Callable[[str], str]
    # 期望的检出方向:哪个指标、往哪边坏
    expect: tuple[str, str]   # (门槛键, "up"=该指标该升高 / "down"=该指标该降低)


CASES: tuple[MutationCase, ...] = (
    MutationCase(
        name="repetitive",
        describe="复读:同一句堆 12 遍",
        mutate=mutate_repetitive,
        expect=("within_repeats_total", "up"),
    ),
    MutationCase(
        name="ai_flavor",
        describe="AI 味:排比 + 套话 + 点题说教",
        mutate=mutate_ai_flavor,
        expect=("mean_flavor", "up"),
    ),
    MutationCase(
        name="truncated",
        describe="篇幅崩:只留 1/6 正文",
        mutate=mutate_truncated,
        expect=("total_chars", "down"),
    ),
    MutationCase(
        name="runaway",
        describe="刹不住:正文放大 6 倍",
        mutate=mutate_runaway,
        expect=("total_chars", "up"),
    ),
)


@dataclass
class MutationResult:
    name: str
    describe: str
    detected: bool
    metric: str
    base_value: float | None
    mutated_value: float | None
    # 有多少项指标确实变坏了(不只是期望那一项)
    worsening_metrics: list[str] = field(default_factory=list)
    note: str = ""


def run_mutations(*, cases: tuple[MutationCase, ...] = CASES) -> list[MutationResult]:
    """对每种退化跑一遍,看指标有没有按预期变坏。

    返回逐案结果;`detected=False` 说明**这套尺子对这个退化不敏感**——
    那是评测体系的缺陷(该补指标),不是被测内容的缺陷。
    """
    base = score_texts([BASELINE_TEXT], label="baseline")
    base_agg = base.aggregate
    results: list[MutationResult] = []

    for case in cases:
        mutated = case.mutate(BASELINE_TEXT)
        run: DeterministicRun = score_texts([mutated], label=case.name)
        metric, direction = case.expect
        b = base_agg.get(metric)
        m = run.aggregate.get(metric)
        detected = _is_worse(b, m, direction)

        # 另外看看「整体」有没有变坏(不只看期望那一项)——若一项都没坏,
        # 说明这个退化在整套指标里是隐形的,更值得警惕。
        worsening = [
            k for k, v in run.aggregate.items()
            if isinstance(v, (int, float)) and isinstance(base_agg.get(k), (int, float))
            and _dir_of(k) == "down" and v > base_agg[k]
        ]
        note = ""
        if not detected:
            note = (
                f"❌ 未检出:{case.describe} 后 {metric} 从 {b} 变为 {m},"
                "没有朝预期方向变化——评测体系对这个退化不敏感"
            )
        results.append(MutationResult(
            name=case.name,
            describe=case.describe,
            detected=detected,
            metric=metric,
            base_value=b,
            mutated_value=m,
            worsening_metrics=worsening,
            note=note,
        ))
    return results


# 「越低越好」的指标(值升高 = 变坏)。与 report.AGG_METRICS 的方向标注同源。
_WORSE_WHEN_HIGHER = {
    "mean_flavor", "within_repeats_total", "repeated_sentences_cross",
    "repeated_phrases_cross", "burstiness_flagged", "metronome_groups_total",
    "tail_summary_total",
}
# 「越高越好」的指标
_WORSE_WHEN_LOWER = {"total_hanzi", "total_chars", "chars_per_chapter"}
# 越接近 1 越好:篇幅比。两边都算坏,但方向由用例给。
_BOTH_BAD = {"mean_target_ratio"}


def _dir_of(key: str) -> str:
    if key in _WORSE_WHEN_HIGHER:
        return "down"     # 越高越坏 → 这个指标的「好方向」是 down
    if key in _WORSE_WHEN_LOWER:
        return "up"
    return "info"


def _is_worse(base, mutated, direction: str) -> bool:
    """退化样本相对基准是否「按方向变坏」。缺值视为未检出。"""
    if not isinstance(base, (int, float)) or not isinstance(mutated, (int, float)):
        return False
    if direction == "up":
        return mutated > base
    if direction == "down":
        return mutated < base
    return False


def format_mutations(results: list[MutationResult]) -> str:
    """给人看的判别力报告。全检出才说明门槛有效。"""
    if not results:
        return "未跑任何退化用例。"
    lines = ["判别力轨(故意写坏,验证指标确实会掉):"]
    for r in results:
        mark = "✓" if r.detected else "✗"
        lines.append(
            f"  {mark} {r.name:12s} {r.metric}: {r.base_value} → {r.mutated_value}"
        )
        if r.note:
            lines.append(f"      {r.note}")
    caught = sum(1 for r in results if r.detected)
    lines.append(f"  {caught}/{len(results)} 种退化被检出")
    if caught == len(results):
        lines.append("  ⇒ 门槛有判别力:这些退化会被 CI 拦下。")
    else:
        lines.append("  ⇒ ⚠ 有退化未被检出,评测体系存在盲区(见上)。")
    return "\n".join(lines)
