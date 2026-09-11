# app/engines/pipeline/chapter_rework.py
# -*- coding: utf-8 -*-
"""分级回炉循环:门禁先行(先修对)→ 精修(再修好)。

从 chapter.py 拆出。这是全书分支最密的一段控制流,拆开的目的是把三条退出路径
说清楚:

  ① 门禁降级    → 隔离,不烧回炉轮(重跑解决不了模型抽风);
  ② 门禁 blocker → 定点修复(patch,一回小调用保住好文)或整章重写;
     同一批 blocker 连续复现 → 止损隔离(重写=重新抽签,消不掉还烧钱);
  ③ 主审未达标   → 带意见重写;同一维度连续两轮无改善 → 停止重写接受当前版本
     (实测 prose 6→6→6 的死锁,靠它破)。

预算共用:patch 轮与重写轮都算 revision_rounds,封顶 review_max_revisions。

**实现分两层**,这是第二次拆(第一次从 chapter.py 拆出来):

- 入口 `review_and_rework` 只剩**循环骨架**(两步之间怎么转);
- 两个步骤各自成函数:`_gate_step` / `_review_step`,各自把三条退出路径写完;
- 状态全在 `_Ctx` 里传来传去,退出后由 `_finish` 一次性把账并进主审结果。

为什么要再拆一次:原来那个 259 行的函数里,门禁段的 `final` 与精修段的
`stalled_dims` 交织在同一层缩进里,读的时候得在 170 行里来回找
`state.revision_rounds` 到底在哪儿被改过。现在「一步只做一件事」——
**每一步的返回值就是它的结论**,循环骨架一眼能看出所有可能的走向。

与 chapter_compose 的关系:本模块通过 `compose` 回调驱动草稿+定稿,不自己
持有 prompt 组装逻辑;`compose(rev_block, draft_label, finalize_label)`
的语义见 chapter_compose.Composer.__call__。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from app.engines.common import degraded_of, is_degraded
from app.engines.consistency.checker import (
    blocker_fingerprint,
    blockers_of,
    check_chapter,
    continuity_score,
    triage_issues,
)
from app.engines.editorial import (
    CONTINUITY_DIM,
    DIMS,
    apply_gate_fixes,
    apply_proofread_fixes,
    build_revision_directive,
    judge_passed,
    proofread_chapter,
    repair_chapter,
    review_chapter,
)

logger = logging.getLogger("jarvis-write.chapter")

# 维度中文名(死锁提示文案用)
_DIM_CN = {
    "plot": "情节", "prose": "文笔", "pacing": "节奏",
    "character": "人物", "continuity": "连续性",
}

# prose 维未达标时的定向重写要求:「AI 腔/套话」靠同一模型自由发挥修不掉
# (实测每轮都因 prose=6 烧满回炉预算),必须把要求落到具体禁则上。
_PROSE_REWRITE_DIRECTIVE = (
    "文笔硬要求(上轮 prose 维未达标,重写必须逐条执行):"
    "①每段以具体画面、动作或对白开笔,禁止以心理独白或情绪陈述开段;"
    "②情绪一律外化成动作与感官细节,不写「他很紧张/她很难过」这类直陈;"
    "③比喻每段至多一处,禁用「仿佛/宛如/像是」连用,禁用「空气中弥漫着」"
    "「不知过了多久」「一瞬间,他明白了」这类万能套话;"
    "④对话删解释性台词,每句要么推进信息要么暴露性格;"
    "⑤长短句交错,连续三句同一结构必改写。"
)


# ---- 可注入接线点(seam) ----
# 这些名字是本模块内所有外部依赖的**唯一**调用入口,好处有三:
#   ① 测试只 patch 定义模块一处即可(与拆解前 chapter.py 的 patch 点对齐);
#   ② 分支覆盖可脚本化(check/review 按轮次返回不同结果),不必真调 LLM;
#   ③ 「这一层依赖了什么」一眼可见,不再散落在长控制流里。
# 生产路径用默认实现;测试用 patch.object 换掉。
_check = check_chapter
_repair = repair_chapter
_review = review_chapter
_proofread = proofread_chapter


def _with_prose_directive(directive: str, scores: dict, threshold: int) -> str:
    """prose 维低于阈值时,把「去 AI 腔」的具体禁则追加进重写指令。

    审校没报这一维(None/缺字段/脏值)视为不适用,原样返回——禁则只该在
    prose 确实挂了的时候出现。
    """
    raw = scores.get("prose")
    if raw is None:
        return directive
    try:
        prose = int(raw)
    except (TypeError, ValueError):
        return directive
    if prose >= threshold:
        return directive
    return f"{directive};{_PROSE_REWRITE_DIRECTIVE}" if directive else _PROSE_REWRITE_DIRECTIVE


# compose 回调语义:注入一段修订块,把当前草稿/定稿换成新的一版。
# 第三/四个参数进"上一版正文"位置时语义见 chapter_compose.Composer。
ComposeFn = Callable[..., Awaitable[tuple[str, str]]]


@dataclass
class ReworkState:
    """回炉段的可变状态。全部字段在循环结束后都还有下游用途。"""

    review_result: dict = field(default_factory=dict)
    revision_rounds: int = 0
    proofread_fixed: int = 0  # 校对累计自动修复的硬伤数(回显给用户看"校对跑过了")
    last_fixed_issues: list[dict] = field(default_factory=list)  # 末轮校对自动修复清单
    repair_rounds: int = 0  # 定点修复轮数(计入 revision_rounds,单独回显)
    last_repairs: dict = field(default_factory=dict)  # 末次定点修复明细
    review_degraded: bool = False  # 主审是否降级:没审成 ≠ 写得差,走隔离不回炉


@dataclass
class ReworkOutcome:
    """回炉段产出:最终的正文 + 门禁结果 + 状态。"""

    draft: str
    final: str
    gate_issues: list[dict]
    state: ReworkState
    reviewed_text: str  # 审校/门禁所对应的正文(守卫改动前的指纹基准)


# ---- 循环的单步结论 ----
# 每一步只回一个结论,循环骨架据此决定去哪;「怎么写」留在各步内部。
_STOP = "stop"    # 退出循环:隔离待复核 / 接受当前版本 / 预算用尽
_REDO = "redo"    # 回到循环顶再过一次门禁(定点修复改过正文,或刚完成一次重写)
_GO_ON = "go_on"  # 门禁干净,进入精修(只有门禁步会返回它)


@dataclass
class _Ctx:
    """回炉循环的可变上下文:把原来散在 259 行里的局部变量收成一处。

    字段分两组:一组是本次生成的**输入**(库/项目/正文/回调),一组是循环过程中
    不断被改写的**账**(轮次、回炉原因、各维上一轮得分、blocker 指纹)。
    """

    # 输入
    db: Session
    project: object
    chapter_number: int
    rolling: str
    compose: ComposeFn
    threshold: int
    auto_revise: bool
    max_revisions: int
    outline_block: str
    draft: str
    final: str
    _report: Callable[[str], None] | None = None

    # 账
    state: ReworkState = field(default_factory=ReworkState)
    review_result: dict = field(default_factory=dict)
    gate_issues: list[dict] = field(default_factory=list)
    patch_tried: bool = False  # 上一轮是否刚做过定点修复(修不掉的连续问题强制重写,防烧轮)
    rework_log: list[dict] = field(default_factory=list)  # 逐轮回炉原因(落快照:意见稳不稳一眼可辨)
    prev_dim_scores: dict[str, int] = field(default_factory=dict)  # 上一轮主审各维得分(判断「无改善」)
    stalled_dims: set[str] = field(default_factory=set)  # 连续 2 轮无改善的维度:不再为它重写
    prev_blocker_fps: set[str] = field(default_factory=set)  # 上一轮 blocker 指纹(识别「同一问题复现」)

    # ---------- 共用小件 ----------
    def notify(self, stage: str) -> None:
        """进度上报:旁路上报绝不能影响生成,失败就丢。"""
        if not self._report:
            return
        try:
            self._report(stage)
        except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
            pass

    def gate_label(self) -> str:
        """门禁段的进度标签(首轮不带轮次,回炉轮带)。"""
        if self.state.revision_rounds == 0:
            return "3/6 一致性门禁"
        return f"3/6 一致性门禁(第 {self.state.revision_rounds}/{self.max_revisions} 轮回炉)"

    def review_label(self) -> str:
        """精修段的进度标签。"""
        if self.state.revision_rounds == 0:
            return "4/6 审校把关"
        return f"4/6 审校把关(第 {self.state.revision_rounds}/{self.max_revisions} 轮回炉)"


async def _gate_step(ctx: _Ctx) -> str:
    """① 一致性门禁(docs/08 §5.4):对照圣经 + 上章契约 + 上章结尾原文。

    有 blocker 不进精修:分诊后定点修复或重写,复查通过才往下走。门禁在落库前,
    拦住的矛盾不会抽进圣经。
    """
    st = ctx.state
    rr = ctx.review_result
    ctx.notify(ctx.gate_label())
    ctx.gate_issues = await _check(
        ctx.db, ctx.project.id, ctx.chapter_number, ctx.final,
        rolling_summary=ctx.rolling,
    )
    blockers = blockers_of(ctx.gate_issues)
    # continuity 随门禁结果先入 scores:精修段靠它判达标;预算烧在门禁段时
    # 主审没跑过,scores 至少带上 continuity 供 API/前端回显
    rr.setdefault("scores", {})["continuity"] = continuity_score(ctx.gate_issues)

    # 门禁降级(LLM 调用失败 / 输出解析失败):绝不能当成「没有 blocker」放行——
    # 那等于模型一超时,安全网就自动撤掉(过去正是这么静默放行的)。
    # 走隔离待人工复核,且不烧回炉轮数:重跑解决不了模型抽风,只会白烧钱。
    if is_degraded(ctx.gate_issues):
        reason = (degraded_of(ctx.gate_issues) or [{}])[0].get("reason", "")
        rr["passed"] = False
        rr["gate_note"] = (
            "一致性检查未能完成,本章未经一致性校验,已隔离待人工复核"
            "(模型/网络恢复后可在问题面板手动触发复查)"
        )
        ctx.rework_log.append({
            "round": st.revision_rounds,
            "trigger": "gate_degraded",
            "note": str(reason)[:120],
        })
        logger.warning("第 %d 章一致性检查降级,隔离待人工复核:%s", ctx.chapter_number, reason)
        return _STOP

    if not blockers:
        ctx.patch_tried = False
        return _GO_ON

    rr["passed"] = False
    blocker_fps = {blocker_fingerprint(b) for b in blockers}
    all_recurring = bool(blocker_fps) and blocker_fps <= ctx.prev_blocker_fps
    ctx.prev_blocker_fps = blocker_fps
    if not ctx.auto_revise or st.revision_rounds >= ctx.max_revisions:
        return _STOP
    if all_recurring:
        # 同一批 blocker 上一轮就原样出现过:重写=重新抽签,消不掉还烧钱。
        # 止损隔离(矛盾照旧不进圣经),「疑似误报」的判断交给人工。
        # 本轮没有花任何重工作量,不计回炉轮数。
        rr["gate_note"] = (
            f"{len(blockers)} 个 blocker 连续 2 轮重写后仍未消除,"
            "疑似检查误报;本章已隔离,请人工判断正文后放行或重写"
        )
        ctx.rework_log.append({
            "round": st.revision_rounds, "trigger": "gate",
            "blockers": [b.get("description", "")[:80] for b in blockers],
            "note": "连续复现,止损隔离",
        })
        logger.info(
            "第 %d 章 blocker 连续复现(%s…),止损隔离",
            ctx.chapter_number, sorted(blocker_fps)[0][:40] if blocker_fps else "",
        )
        return _STOP

    st.revision_rounds += 1
    # 分诊:全部可定点修且上一轮没刚修过 → patch(一次小调用,保住好文);
    # 否则整章重写。修完不在这里复查——回到循环顶,门禁说了算。
    if not ctx.patch_tried and triage_issues(blockers) == "patch":
        ctx.patch_tried = True
        st.repair_rounds += 1
        ctx.notify(
            f"3/6 一致性门禁(第 {st.revision_rounds}/{ctx.max_revisions} 轮·定点修复)"
        )
        fixes = await _repair(ctx.chapter_number, ctx.final, blockers)
        new_final, applied, failed = apply_gate_fixes(ctx.final, fixes)
        if applied:
            ctx.final = new_final
            st.last_repairs = {"applied": applied, "failed": failed}
            logger.info(
                "第 %d 章门禁定点修复:%d 处(失配 %d 处),回门禁复查",
                ctx.chapter_number, len(applied), len(failed),
            )
            return _REDO
        logger.info(
            "第 %d 章门禁问题不可定点修(%d 条修复全部未应用),转整章重写",
            ctx.chapter_number, len(fixes),
        )
    ctx.patch_tried = False
    ctx.rework_log.append({
        "round": st.revision_rounds, "trigger": "gate",
        "blockers": [b.get("description", "")[:80] for b in blockers],
    })
    logger.info(
        "第 %d 章门禁拦截 %d 个 blocker,第 %d/%d 轮回炉(重写)",
        ctx.chapter_number, len(blockers), st.revision_rounds, ctx.max_revisions,
    )
    directive = build_revision_directive(_gate_merged_review(rr, blockers))
    ctx.draft, ctx.final = await ctx.compose(
        _revision_block(directive, ctx.final),
        f"3/6 一致性门禁(第 {st.revision_rounds}/{ctx.max_revisions} 轮回炉·重写草稿)",
        f"3/6 一致性门禁(第 {st.revision_rounds}/{ctx.max_revisions} 轮回炉·定稿)",
    )
    return _REDO


async def _review_step(ctx: _Ctx) -> str:
    """② 门禁干净,精修:校对硬伤自修 + 主审四维达标判定。"""
    st = ctx.state
    ctx.notify(ctx.review_label())
    # 校对硬伤:错字/语病/标点/重复,精确替换自修(幻觉片段已在引擎里过滤)
    proof = await _proofread(ctx.final)
    round_fixed: list[dict] = []
    if proof["issues"]:
        ctx.final, _applied, _failed = apply_proofread_fixes(ctx.final, proof["issues"])
        st.proofread_fixed += len(_applied)
        # 留下真正修掉的那几条(带类型/理由),供编辑部「校对」tab 回显
        applied_originals = {a["original"] for a in _applied}
        round_fixed = [it for it in proof["issues"] if it["original"] in applied_originals]
    st.last_fixed_issues = round_fixed

    # 主审打分(四维);continuity 已由门禁段写入(干净 → 9)
    rr = await _review(ctx.final, ctx.outline_block)
    ctx.review_result = rr
    st.review_result = rr
    rr["scores"]["continuity"] = continuity_score(ctx.gate_issues)
    # 主审降级(输出解析失败):四维是被「没解析出来」压成 0 的,不是真的写得差。
    # 不回炉——重写解决不了解析问题,只会白烧钱;走隔离待人工复核。
    if rr.get("degraded"):
        st.review_degraded = True
        rr["passed"] = False
        rr["review_note"] = (
            "主审评分未能完成(输出解析失败),本章未经审校评分,已隔离待人工复核"
        )
        logger.warning(
            "第 %d 章主审降级,隔离待人工复核:%s",
            ctx.chapter_number, rr.get("degraded_reason", ""),
        )
        return _STOP

    # 达标判定:五维阈值硬判(阈值调得再低,blocker 也已在①被拦)
    passed = judge_passed(rr["scores"], ctx.threshold)
    rr["passed"] = passed
    if passed:
        return _STOP
    if not ctx.auto_revise or st.revision_rounds >= ctx.max_revisions:
        return _STOP

    # ---- 回炉原因记账:同一维度连续 2 轮无改善 → 退出重写原因集 ----
    # 重写对同一个模型就是重新抽签:prose 6→6→6 的死锁靠它破——第 2 轮
    # 还停在原地,就不再为这个维度烧草稿+定稿(实测 4 章 12 轮 prose 纹丝不动)。
    scores_now = rr["scores"]
    failing = [
        d for d in (*DIMS, CONTINUITY_DIM)
        if int(scores_now.get(d) or 0) < ctx.threshold
    ]
    st_stalled = ctx.stalled_dims & set(failing)  # 已达标的维度不再算停滞
    retryable: list[str] = []
    for d in failing:
        now_v, prev_v = int(scores_now.get(d) or 0), ctx.prev_dim_scores.get(d)
        if prev_v is not None and now_v <= prev_v:
            st_stalled.add(d)
        elif prev_v is not None and now_v > prev_v:
            st_stalled.discard(d)  # 有改善,再给一轮机会
        if d not in st_stalled:
            retryable.append(d)
    ctx.stalled_dims = st_stalled
    ctx.rework_log.append({
        "round": st.revision_rounds + 1, "trigger": "review",
        "failing": list(failing), "stalled": sorted(ctx.stalled_dims),
    })
    if not retryable:
        # 所有未达标维度都连续两轮无改善:再重写注定同样结果,接受当前版本
        rr["stall_note"] = (
            "未达标维度连续 2 轮回炉无改善,已停止重写并接受当前版本;"
            "建议写手与审校使用不同模型,或适当调低达标线"
        )
        logger.info(
            "第 %d 章 %s 维连续无改善,停止重写,接受当前版本",
            ctx.chapter_number, "/".join(failing),
        )
        return _STOP

    st.revision_rounds += 1
    logger.info(
        "第 %d 章未通过(五维=%s,阈值=%d,待改维度=%s),第 %d/%d 轮回炉",
        ctx.chapter_number, rr["scores"], ctx.threshold,
        "/".join(retryable), st.revision_rounds, ctx.max_revisions,
    )
    directive = build_revision_directive(rr)
    if "prose" in retryable:
        directive = _with_prose_directive(directive, rr.get("scores") or {}, ctx.threshold)
    ctx.draft, ctx.final = await ctx.compose(
        _revision_block(directive, ctx.final),
        f"4/6 审校把关(第 {st.revision_rounds}/{ctx.max_revisions} 轮回炉·草稿)",
        f"4/6 审校把关(第 {st.revision_rounds}/{ctx.max_revisions} 轮回炉·定稿)",
    )
    ctx.prev_dim_scores = {
        d: int(scores_now.get(d) or 0) for d in (*DIMS, CONTINUITY_DIM)
    }
    return _REDO


def _finish(ctx: _Ctx) -> ReworkOutcome:
    """收尾:把回炉过程的账(轮次/定点修复/回炉原因/停滞提示)并进主审结果。"""
    st = ctx.state
    rr = ctx.review_result
    st.review_result = rr
    rr["revision_rounds"] = st.revision_rounds
    rr["repair_rounds"] = st.repair_rounds
    rr["repairs"] = st.last_repairs
    rr["rework_log"] = ctx.rework_log
    rr["threshold"] = ctx.threshold
    rr["proofread_fixed"] = st.proofread_fixed
    # 死锁提示:停滞维度显式告知(模型配比可能系统性不可达),决策留给作者
    if ctx.stalled_dims:
        rr["hints"] = [
            f"「{_DIM_CN.get(d, d)}」维连续多轮回炉无改善:当前写手/审校模型配比下"
            f"该维度可能无法稳定达到阈值 {ctx.threshold}。建议写手与审校使用不同模型,"
            "或在项目设置中适当调低达标线。"
            for d in sorted(ctx.stalled_dims)
        ]
    logger.info(
        "第 %d 章审校+门禁完成:通过=%s,五维=%s,blocker=%d,回炉 %d 轮(定点修 %d)",
        ctx.chapter_number, rr.get("passed"),
        rr.get("scores"), len(blockers_of(ctx.gate_issues)),
        st.revision_rounds, st.repair_rounds,
    )
    # 审校/门禁对应的正文(字数守卫可能在其后改动,指纹以此为准)
    return ReworkOutcome(
        draft=ctx.draft, final=ctx.final, gate_issues=ctx.gate_issues,
        state=st, reviewed_text=ctx.final,
    )


async def review_and_rework(
    db: Session,
    project,
    chapter_number: int,
    draft: str,
    final: str,
    *,
    outline,
    rolling: str,
    compose: ComposeFn,
    report=None,
) -> ReworkOutcome:
    """门禁 + 精修的回炉循环(详见模块头)。返回 ReworkOutcome。

    compose(rev_block, draft_label, finalize_label) 必须返回新的 (draft, final);
    本轮无需真正重写时(如首轮已达标)不会被调用。

    循环骨架只有三条线,每条都是「这一步的结论」:
    门禁要停 → 退出;门禁要重写 → 回顶再过一次门禁;门禁干净 → 进精修;
    精修要停 → 退出;精修要重写 → 回顶再过一次门禁(重写过的稿子必须重新受检)。
    """
    ctx = _Ctx(
        db=db,
        project=project,
        chapter_number=chapter_number,
        rolling=rolling,
        compose=compose,
        threshold=project.review_pass_threshold,
        auto_revise=project.review_auto_revise,
        max_revisions=project.review_max_revisions,
        outline_block=(
            f"标题:{outline.title}\n目的:{outline.chapter_purpose}\n概要:{outline.summary}"
        ),
        draft=draft,
        final=final,
        _report=report,
    )
    while True:
        gate = await _gate_step(ctx)
        if gate == _STOP:
            break
        if gate == _REDO:
            continue
        if await _review_step(ctx) == _STOP:
            break
    return _finish(ctx)


# ---- 以下两个 helper 与回炉段强耦合(只被它调用),故随它一起搬 ----

def _gate_merged_review(review_result: dict, blockers: list[dict]) -> dict:
    """把门禁 blocker 问题并入主审结果,供 build_revision_directive 拼修订指令。"""
    merged = dict(review_result)
    merged["suggestions"] = list(review_result.get("suggestions") or []) + [
        {
            "evidence": i.get("evidence") or "",
            "issue": f"一致性矛盾({i.get('type') or 'state'}):{i.get('description')}",
            "fix": i.get("suggestion") or "",
        }
        for i in blockers
    ]
    return merged


def _revision_block(
    revision: str | None, previous_text: str, *, outline_changed: bool = False
) -> str:
    """重写意见注入块。

    - outline_changed=True(大纲改过后正文失配):不再注入旧正文节选——旧文基于
      旧大纲,注入会把模型锚回旧情节。改为明确指令"按新蓝图重新构思",用户补充
      意见(有则)一并带上。即使没有意见也生成该块:失配章的重写本质是重新生成。
    - 常规重写(大纲未变):上一版正文截断为前 _REVISION_EXCERPT_CHARS 字作反面
      参照,避免 token 爆炸;无意见则不生成。
    """
    revision = (revision or "").strip()
    if outline_changed:
        block = (
            "【按新大纲重写】本章大纲已更新,上一版正文基于旧大纲,与当前蓝图失配。\n"
            "请完全以上方最新蓝图为准重新构思本章情节,不要延续、不要修补旧版正文"
            "的情节安排;旧版正文中与旧大纲绑定的桥段应直接舍弃。\n"
        )
        if revision:
            block += f"用户补充意见(在满足新蓝图的前提下采纳):\n{revision}\n"
        return block
    if not revision or not previous_text.strip():
        return ""
    excerpt = previous_text[:_REVISION_EXCERPT_CHARS]
    if len(previous_text) > _REVISION_EXCERPT_CHARS:
        excerpt += "……(后略)"
    return (
        "【重写要求】这是重写:上一版正文用户不满意,修改意见如下:\n"
        f"{revision}\n"
        "请在保持本章蓝图、人物状态与伏笔约束不变的前提下,针对以上意见改进。\n\n"
        "【上一版正文(反面参照,仅供对照问题,不可照抄)】\n"
        f"{excerpt}"
    )


_REVISION_EXCERPT_CHARS = 1500  # 重写时上一版正文注入草稿 prompt 的截断长度

__all__ = [
    "ReworkOutcome",
    "ReworkState",
    "review_and_rework",
    "_gate_merged_review",
    "_revision_block",
    "_with_prose_directive",
    "_PROSE_REWRITE_DIRECTIVE",
    "_REVISION_EXCERPT_CHARS",
    "_DIM_CN",
]
