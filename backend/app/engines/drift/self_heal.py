# app/engines/drift/self_heal.py
# -*- coding: utf-8 -*-
"""治漂闭环:负向硬门(越线毙+重生)+ 正向味道自愈(仿 polish.deai_self_heal)。

对应用户拍板的「硬门:越线自动毙+重生(推荐)」——用户从不看到跑偏版本。

两条闭环:
1. enforce_genre_gate 负向硬门:生成→regex 预筛→LLM-judge 精筛确认→确认越界即
   毙+重生,限轮兜底。regex 高召回(宁多框),judge 去误报(把「觉醒了对文学的
   热爱」这类假阳性放行),只对「确认为真」的越界才 kill+regen。
2. taste_self_heal 正向味道自愈:必须有未落地→定向重写→复测→限轮收敛,
   与 polisher.deai_self_heal 同纪律:只在「确实改好了且没引入新越界」时采纳,
   篇幅越界/未改好/引入新禁忌一律丢弃本轮,绝不返回更差版本。

judge 失败方向:精筛调用/解析失败时**从严**(fail-toward-confirm,当作真越界),
宁可多花一轮重生,也不放跑偏溜过去——因为这是核心卖点,一定不能偏。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from app.engines.consistency.extractor import parse_llm_json
from app.engines.drift.genre_fit import GenreFitReport, GenreHit, genre_fit_report
from app.llm.router import Task, get_adapter_for

logger = logging.getLogger("jarvis-write.drift")

# 题材模式 → 中文标签(仅用于 judge 判词的人话措辞;与 schemas.dna.DNA_MODES 保持一致)。
_MODE_LABEL = {"realistic": "现实向", "fantasy": "幻想向", "mixed": "混合向"}

# 味道自愈的篇幅安全阀(沿用 deai_self_heal 的区间):越界视为跑偏,丢弃本轮。
_HEAL_LEN_LO, _HEAL_LEN_HI = 0.75, 1.25
# judge 证据里每个 label 最多带几条命中片段(防 prompt 膨胀)。
_MAX_EVIDENCE_PER_LABEL = 3


# LLM 精筛判词:regex 是高召回预筛,这里做「是不是真越界」的精筛,专杀假阳性。
_JUDGE_PROMPT = """你是题材一致性审校。本作被设定为「{mode_label}」。
下面是正则预筛「疑似越界(出现了不该出现的非本模式元素)」的命中项。正则会误报——
比如「觉醒了对文学的热爱」只是比喻、「穿越马路」是日常动作,都**不算**越界。
请逐项判断:该命中在其语境里,是否**真的**引入了与「{mode_label}」冲突的设定
(如现实向里真出现了超能力/系统/穿越重生/修仙魔法/灵异等)。

疑似命中(元素名 → 命中片段):
{evidence}

只输出 JSON,不要解释:
{{"violations": ["确认为真越界的元素名", ...]}}
若全部为误报,输出 {{"violations": []}};元素名必须原样取自上面给出的名字。"""


def _evidence_block(candidate_labels: list[str], hits: list[GenreHit] | None) -> str:
    """把命中片段按 label 归组渲染成 judge 证据块。无片段时只列元素名。"""
    by_label: dict[str, list[str]] = {lbl: [] for lbl in candidate_labels}
    for h in hits or []:
        if h.label in by_label and len(by_label[h.label]) < _MAX_EVIDENCE_PER_LABEL:
            by_label[h.label].append(f"「{h.phrase}」…{h.snippet}…")
    lines = []
    for lbl in candidate_labels:
        snips = by_label.get(lbl) or []
        lines.append(f"- {lbl}:" + ("；".join(snips) if snips else "(见正文)"))
    return "\n".join(lines)


async def confirm_forbidden(
    text: str,
    candidate_labels: list[str],
    mode: str = "",
    hits: list[GenreHit] | None = None,
) -> list[str]:
    """LLM 精筛:从 regex 预筛的疑似越界里,确认哪些是**真**越界。

    返回确认为真的元素名子集(空 = 全是误报,放行)。
    失败从严:调用/解析异常或结构不可辨 → 返回全部候选(当作真越界),
    宁可多重生一轮也不放跑偏溜过——核心卖点一定不能偏。
    """
    labels = [l for l in dict.fromkeys(candidate_labels) if l]  # 去重保序、去空
    if not labels:
        return []
    prompt = _JUDGE_PROMPT.format(
        mode_label=_MODE_LABEL.get((mode or "").strip(), "本题材"),
        evidence=_evidence_block(labels, hits),
    )
    try:
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
        data = parse_llm_json(raw)
    except Exception:  # noqa: BLE001 — 判词失败不该放跑偏溜过,从严当作真越界
        logger.warning("题材越界精筛调用/解析失败,从严按真越界处理", exc_info=True)
        return labels
    if not isinstance(data, dict) or not data:
        return labels  # 解析出空 → 无法确认 → 从严
    raw_viol = data.get("violations")
    if raw_viol is None:
        raw_viol = data.get("confirmed")
    if raw_viol is None:
        return labels  # 关键字段缺失 → 无法确认 → 从严
    confirmed = {str(x).strip() for x in raw_viol if str(x).strip()}
    return [l for l in labels if l in confirmed]


async def enforce_genre_gate(
    generate_fn: Callable[[int], Awaitable[object]],
    to_text: Callable[[object], str],
    *,
    mode: str = "",
    must: tuple[str, ...] = (),
    must_not: tuple[str, ...] = (),
    initial: object | None = None,
    max_regens: int = 2,
    judge: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """负向硬门:生成一个单元,越线(确认为真越界)即毙+重生,限轮兜底。

    - generate_fn(attempt) -> 单元:attempt=0 为首次(仅 initial 为空时调用),
      attempt>=1 为重生;调用方可据 attempt 递进强化「上次跑偏,这次绝不能有」。
    - to_text(单元) -> 待检文本。
    - initial:已生成的单元(如首轮列表里的一个),非空则先检它,省一次生成。
    - judge:是否走 LLM 精筛(关掉则 regex 命中即视为越界,更狠更省)。

    返回 {result, report, regens, blocked, confirmed_labels}:
    - blocked=True 表示重生用尽仍确认越界——调用方决定丢弃/降级/带警告展示
      (概念列表场景应直接丢弃该条,即「用户从不看到跑偏版本」)。
    """
    regens = 0
    unit = initial if initial is not None else await generate_fn(0)
    while True:
        report = genre_fit_report(
            to_text(unit) or "", mode=mode, must=must, must_not=must_not
        )
        if not report.has_forbidden():
            return _gate_result(unit, report, regens, False, [])

        candidates = report.forbidden_labels()
        confirmed = (
            await confirm_forbidden(to_text(unit) or "", candidates, mode, report.forbidden)
            if judge else candidates
        )
        if not confirmed:  # 全是误报,judge 放行
            return _gate_result(unit, report, regens, False, [])

        if regens >= max_regens:  # 重生用尽仍越界 → 拦下,交调用方处置
            logger.info("题材硬门:重生 %d 次仍越界 %s,拦下", regens, confirmed)
            return _gate_result(unit, report, regens, True, confirmed)

        regens += 1
        if progress:
            try:
                progress(f"题材越界(命中 {'、'.join(confirmed)}),毙+重生第 {regens} 次")
            except Exception:  # noqa: BLE001 — 进度上报绝不影响主流程
                pass
        unit = await generate_fn(regens)


def _gate_result(unit, report, regens, blocked, confirmed) -> dict:
    return {
        "result": unit,
        "report": report,
        "regens": regens,
        "blocked": blocked,
        "confirmed_labels": confirmed,
    }


async def taste_self_heal(
    text: str,
    rewrite_fn: Callable[[str, GenreFitReport], Awaitable[str]],
    *,
    mode: str = "",
    must: tuple[str, ...] = (),
    must_not: tuple[str, ...] = (),
    max_rounds: int = 2,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, GenreFitReport, GenreFitReport]:
    """正向味道自愈:必须有未落地 → 定向重写 → 复测 → 限轮收敛。

    与 polisher.deai_self_heal 同纪律(只在「确实改好了」才采纳):
    - rewrite_fn 调用异常 / 输出为空 → 丢弃本轮
    - 篇幅越界(缩水或膨胀超阈值)→ 丢弃本轮
    - 味道覆盖未提升,或引入了新的疑似越界 → 丢弃本轮
    任一轮丢弃即停,绝不返回比原文更差的版本。返回 (最终正文, 初检, 末检)。
    """
    before = genre_fit_report(text, mode=mode, must=must, must_not=must_not)
    if not before.taste_notes:  # 必须有已全覆盖,无需自愈
        return text, before, before

    best_text, best_report = text, before
    for i in range(max_rounds):
        if progress:
            try:
                progress(f"味道自愈(第 {i + 1} 轮,当前覆盖 {best_report.taste_score})")
            except Exception:  # noqa: BLE001
                pass
        try:
            rewritten = (await rewrite_fn(best_text, best_report) or "").strip()
        except Exception:  # noqa: BLE001 — 重写失败不该毁掉已有正文
            logger.warning("味道定向重写调用失败,保留当前版本", exc_info=True)
            break
        if not rewritten:
            break
        ratio = len(rewritten) / max(len(best_text), 1)
        if not (_HEAL_LEN_LO <= ratio <= _HEAL_LEN_HI):
            logger.info("味道重写篇幅越界(比例 %.2f),丢弃本轮", ratio)
            break
        after = genre_fit_report(rewritten, mode=mode, must=must, must_not=must_not)
        # 采纳条件:必须有覆盖提升,且没有引入新的疑似越界
        if after.taste_score <= best_report.taste_score or len(after.forbidden) > len(best_report.forbidden):
            logger.info(
                "味道重写未改好(覆盖 %d→%d,越界 %d→%d),丢弃本轮",
                best_report.taste_score, after.taste_score,
                len(best_report.forbidden), len(after.forbidden),
            )
            break
        best_text, best_report = rewritten, after
        if not best_report.taste_notes:  # 全覆盖,提前收敛
            break
    return best_text, before, best_report
