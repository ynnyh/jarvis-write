# app/engines/evolution/reflect.py
# -*- coding: utf-8 -*-
"""复盘反思:证据包 → 候选记忆条目(docs/36 §4)。

纪律(来自传国玉玺案例与 docs/34 的教训):
- 只提炼**有证据支撑**的条目,没有证据的宁可不写——无出处泛化是记忆污染的头号来源;
- 教训只从「作者删改 / 重写 / 反复补充说明」里来,作者没抱怨过的不算教训;
- content 写给下一次开书时的 AI 看,必须是可执行的具体规则,不是「文笔流畅」式空话。

模型输出一律经 clean_memories 收敛(白名单 kind / 截断 / 对已有记忆去重)才落库;
confirm 档落 pending 等作者拍板,auto 档直接 confirmed(仍可见可删)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import EvolutionMemory
from app.engines.common import ask_llm_json
from app.llm.router import Task, get_adapter_for

# 每次复盘最多入库几条:注入上限 8,提炼再多也是浪费
_MAX_MEMORIES = 8
_CONTENT_MAX = 200
_EVIDENCE_MAX = 120
_SUMMARY_MAX = 300
_KINDS = ("style", "lesson", "preference")
_CONFIDENCE = ("high", "medium", "low")

RETROSPECTIVE_PROMPT = """\
你是 AI 写作工坊的复盘教练。作者刚用本工坊写完(或写到中途的)一本书,下面是\
这次写作的**证据包**:作者采纳了什么样的正文、亲自动手删改了什么、过程中说过什么。\
请从中提炼「下一本书应该记住的东西」。

【作品】《{title}》 题材:{genre} / 模式:{mode} / 定稿 {approved}/{total} 章,约 {words} 字
{samples_section}{edits_section}{directives_section}请输出 JSON(不要 markdown 围栏,不要解释):
{{"summary": "一两句总评:这本书的文风底色与作者取向",
  "memories": [
    {{"kind": "style", "content": "…", "evidence": "…", "confidence": "high"}}
  ]}}

字段说明:
- kind 三选一:style=文风特征(下次写时延续)/ lesson=教训(下次要避免,只从删改、\
重写、反复补充说明里提炼)/ preference=偏好(下次延续的题材或写法取向)。
- content:一句**具体可执行**的规则,不超过 80 字,写给下一次开书时的 AI 看。\
「段尾停在动作上,不做总结升华」是合格的;「文笔流畅」「人物生动」是废话,禁止。
- evidence:支撑这条的出处,不超过 60 字(例:第12章草稿删掉了全部段尾抒情)。
- confidence:high / medium / low,你对证据强度的自评。

要求:
- 3 到 8 条,按证据强度从强到弱排;没有证据支撑的宁可不写。
- 教训(lesson)必须有删改/重写类证据;作者没有表达过不满的不要脑补成教训。
- 全部中文。
"""


def _render_samples(samples: list[dict[str, Any]]) -> str:
    if not samples:
        return ""
    blocks = [f"—— 第{s['chapter']}章(节选) ——\n{s['text']}" for s in samples]
    return "【作者采纳的正文样本(已是作者认可的样子)】\n" + "\n".join(blocks) + "\n\n"


def _render_edits(edits: list[dict[str, Any]]) -> str:
    if not edits:
        return ""
    blocks = [
        f"—— 第{e['chapter']}章的主要删改 ——\n" + "\n".join(f"· {s}" for s in e["spans"])
        for e in edits
    ]
    return "【作者亲自动手删改的痕迹(他不要的东西,教训从这里来)】\n" + "\n".join(blocks) + "\n\n"


def _render_directives(directives: list[str]) -> str:
    if not directives:
        return ""
    return "【作者在生成过程中下的指令(新→旧)】\n" + "\n".join(f"· {d}" for d in directives) + "\n\n"


def build_retrospective_prompt(evidence: dict[str, Any]) -> str:
    stats = evidence["chapter_stats"]
    meta = evidence["meta"]
    return RETROSPECTIVE_PROMPT.format(
        title=meta["title"],
        genre=meta["genre"],
        mode=meta["mode"],
        approved=stats["approved"],
        total=stats["total"],
        words=stats["words"],
        samples_section=_render_samples(evidence["samples"]),
        edits_section=_render_edits(evidence["edits"]),
        directives_section=_render_directives(evidence["directives"]),
    )


def _clean_str(value: Any, cap: int) -> str:
    return " ".join(str(value or "").split())[:cap]


def clean_memories(
    data: dict[str, Any],
    existing: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """把模型输出收敛成可落库形状:(summary, items)。

    白名单 kind / confidence,截断长度,限量条数,并对 existing(该用户已有记忆的
    content 归一化集合)去重——同一句教训记两遍,注入时就是双倍权重。
    """
    seen = {e.strip() for e in (existing or [])}
    summary = _clean_str(data.get("summary"), _SUMMARY_MAX)
    items: list[dict[str, Any]] = []
    for raw in data.get("memories") or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        if kind not in _KINDS:
            kind = "preference"
        content = _clean_str(raw.get("content"), _CONTENT_MAX)
        if not content or content in seen:
            continue
        seen.add(content)
        confidence = str(raw.get("confidence") or "").strip()
        if confidence not in _CONFIDENCE:
            confidence = "medium"
        items.append({
            "kind": kind,
            "content": content,
            "evidence": _clean_str(raw.get("evidence"), _EVIDENCE_MAX),
            "confidence": confidence,
        })
        if len(items) >= _MAX_MEMORIES:
            break
    return summary, items


async def run_retrospective(
    evidence: dict[str, Any],
    existing: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """跑一次复盘 LLM 调用,返回 (summary, items, parse_err)。

    existing 是该用户已有记忆的 content 列表,提炼时直接去重(同一句教训记两遍,
    注入时就是双倍权重)。parse_err 非空表示模型输出不可用(调用方应放弃本次,
    不得落库);adapter 的网络/配置异常不吞,原样上抛(与 intent-readback 同口径)。
    """
    prompt = build_retrospective_prompt(evidence)
    data, parse_err = await ask_llm_json(
        get_adapter_for(Task.EVOLUTION), prompt, label="写作复盘",
        contract={"memories": list},
    )
    if parse_err:
        return "", [], parse_err
    # 全部重复或没有可用条目时返回空列表(不报错):调用方如实返回「没有新东西」,
    # 让作者再试或换本书复盘,而不是 502 误导成系统故障。
    summary, items = clean_memories(data, existing)
    return summary, items, None


def persist_memories(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    summary: str,
    items: list[dict[str, Any]],
    auto_confirm: bool,
) -> list[EvolutionMemory]:
    """按自治档位落库:confirm→pending,auto→confirmed。返回本次新落的行。"""
    source = {"summary": summary}
    rows = [
        EvolutionMemory(
            user_id=user_id,
            project_id=project_id,
            kind=item["kind"],
            content=item["content"],
            evidence=item["evidence"],
            confidence=item["confidence"],
            status="confirmed" if auto_confirm else "pending",
            source=source,
        )
        for item in items
    ]
    db.add_all(rows)
    return rows
