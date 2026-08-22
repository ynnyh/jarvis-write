# app/engines/pipeline/handoff.py
# -*- coding: utf-8 -*-
"""章末交接契约:提取、校验、注入(见 docs/08 §5.2「章末交接契约」)。

闭环:每章定稿后提取章末瞬态(时间/地点/人物即时状态/未决线索)落 chapter_states
→ 生成下一章时把上一章契约渲染成文本块注入草稿 prompt(与 recent_tail 并存:
原文供语感,契约供事实)。

失败降级(docs/08 §4):LLM 调用 / JSON 解析 / 结构校验任一失败,落一行
extract_status=failed 留痕,绝不抛异常阻塞主流程;下章注入时无有效契约
(无记录/失败/正文指纹不符)则回退为不注入,行为与旧版一致。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState
from app.engines.consistency.extractor import parse_llm_json
from app.engines.editorial import content_hash
from app.prompts.consistency import HANDOFF_CONTRACT_PROMPT

logger = logging.getLogger("jarvis-write.handoff")

_MAX_CHARACTERS = 6      # 与提取 prompt 约定一致
_MAX_KNOWS = 5
_MAX_OPEN_THREADS = 8
_CONTRACT_TEXT_CHARS = 12000  # 提取注入正文截断,防超长(对齐 extractor)


def _s(value) -> str | None:
    """归一字符串字段:非空去空白,空/非字符串 → None。"""
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v or None


def validate_contract(data: dict) -> dict | None:
    """结构校验 + 归一化 LLM 提取结果;无效返回 None(视为提取失败)。

    宽容归一(缺字段补 null、超量截断),但三项核心(剧情时间/地点/人物)全空
    时判定本次提取无价值,返回 None 走失败留痕。
    """
    if not isinstance(data, dict) or not data:
        return None

    characters: list[dict] = []
    raw_chars = data.get("characters")
    if not isinstance(raw_chars, list):
        raw_chars = []
    for c in raw_chars[:_MAX_CHARACTERS]:
        if not isinstance(c, dict):
            continue
        name = _s(c.get("name"))
        if not name:
            continue
        knows = c.get("knows")
        characters.append({
            "name": name,
            "location": _s(c.get("location")),
            "physical": _s(c.get("physical")),
            "emotional": _s(c.get("emotional")),
            "doing": _s(c.get("doing")),
            "knows": [s for s in (_s(x) for x in (knows or [])[:_MAX_KNOWS]) if s]
            if isinstance(knows, list) else [],
            "unresolved_intent": _s(c.get("unresolved_intent")),
        })

    threads = data.get("open_threads")
    contract = {
        "in_story_time": _s(data.get("in_story_time")),
        "location": _s(data.get("location")),
        "scene_continues": bool(data.get("scene_continues")),
        "ambient": _s(data.get("ambient")),
        "characters": characters,
        "open_threads": [s for s in (_s(x) for x in (threads or [])[:_MAX_OPEN_THREADS]) if s]
        if isinstance(threads, list) else [],
        "time_jump_hint": _s(data.get("time_jump_hint")) or "none",
    }
    if not (contract["in_story_time"] or contract["location"] or characters):
        return None
    return contract


def format_contract_block(contract: dict, prev_chapter_number: int) -> str:
    """把上一章契约渲染成草稿 prompt 注入文本块。"""
    lines = [
        f"【上一章(第{prev_chapter_number}章)章末交接契约——上一章结尾的结构化状态记录。"
        "本章开头衔接必须与之吻合:时间、地点、环境氛围(天气/光线/声音)、"
        "在场人物的身体/情绪/正在做的事不得与之冲突;"
        "如需时间跳跃,开头要自然交代,别让读者觉得状态被凭空重置】"
    ]
    if t := contract.get("in_story_time"):
        lines.append(f"- 剧情时间:{t}")
    if loc := contract.get("location"):
        lines.append(f"- 章末地点:{loc}")
    lines.append(
        "- 场景延续:" + ("是(本章应紧接上一幕继续)" if contract.get("scene_continues")
                         else "否(可切换场景或时间)")
    )
    if amb := contract.get("ambient"):
        lines.append(
            f"- 章末环境氛围:{amb}"
            "(紧接同一场景/时段则天气·光线·声音须与此一致;确有时间或场景跳转可自然过渡,"
            "但别无缘由地翻转——如上一章“没有一只鸟雀”,本章却“被鸟叫吵醒”)"
        )
    characters = contract.get("characters") or []
    if characters:
        lines.append("- 人物即时状态:")
        for c in characters:
            seg = f"  · {c['name']}"
            if c.get("location"):
                seg += f"@{c['location']}"
            details = []
            if c.get("physical"):
                details.append(f"身体:{c['physical']}")
            if c.get("emotional"):
                details.append(f"情绪:{c['emotional']}")
            if c.get("doing"):
                details.append(f"正在:{c['doing']}")
            if c.get("knows"):
                details.append("已知:" + "、".join(c["knows"]))
            if c.get("unresolved_intent"):
                details.append(f"未了意图:{c['unresolved_intent']}")
            if details:
                seg += "(" + ";".join(details) + ")"
            lines.append(seg)
    if threads := contract.get("open_threads"):
        lines.append("- 未决线索:" + "、".join(threads))
    hint = contract.get("time_jump_hint")
    if hint and hint != "none":
        lines.append(f"- 时间跳跃提示:{hint}(本章开头应有相应交代)")
    return "\n".join(lines)


def _fresh_contract(row: ChapterState | None, chapter: Chapter) -> dict | None:
    """读取契约:仅当提取成功且指纹与章节当前正文一致时返回,否则 None(回退现状)。"""
    if row is None or row.extract_status != "ok" or not row.contract:
        return None
    if row.content_hash != content_hash(chapter.final_content or ""):
        return None
    try:
        contract = json.loads(row.contract)
    except (ValueError, TypeError):
        return None
    return contract if isinstance(contract, dict) else None


def load_handoff_block(db: Session, project_id: int, chapter_number: int) -> str:
    """生成第 N 章时取第 N-1 章契约渲染注入块;无有效契约 → 空串(回退现状,不报错)。"""
    if chapter_number <= 1:
        return ""
    prev = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number - 1)
        .first()
    )
    if prev is None or not prev.final_content:
        return ""
    row = db.query(ChapterState).filter(ChapterState.chapter_id == prev.id).first()
    contract = _fresh_contract(row, prev)
    if contract is None:
        return ""
    return format_contract_block(contract, prev.chapter_number)


def handoff_gap(db: Session, project_id: int, chapter_number: int) -> str | None:
    """上一章「本应有契约却缺失/失效」时返回人话原因,否则 None。

    load_handoff_block 把「第一章/无上一章」与「上一章有正文却没有效契约」都塌成
    空串静默降级(#5 的病根之一:静默=无锚,门禁与开头衔接都少了环境/状态对照)。
    本函数把后者单拎出来,供写前审核冒一条可见警告,不再无声吞掉。
    第一章 / 上一章无正文 → None(本就不需要契约,不算缺失)。
    """
    if chapter_number <= 1:
        return None
    prev = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number - 1)
        .first()
    )
    if prev is None or not prev.final_content:
        return None
    row = db.query(ChapterState).filter(ChapterState.chapter_id == prev.id).first()
    if row is None:
        return "上一章从未提取章末交接契约"
    if row.extract_status != "ok" or not row.contract:
        return "上一章章末交接契约提取失败(已留痕)"
    if row.content_hash != content_hash(prev.final_content or ""):
        return "上一章正文改动后章末契约已失效(指纹不符)"
    return None


def handoff_payload(db: Session, chapter: Chapter) -> dict:
    """章节详情 API 透出用:{status, contract, error}。

    status:none(从未提取)/ ok / failed;contract 仅在指纹对应当前正文时返回,
    正文被手改/回滚后为 None(与审校快照的失效逻辑一致)。
    """
    row = db.query(ChapterState).filter(ChapterState.chapter_id == chapter.id).first()
    if row is None:
        return {"status": "none", "contract": None, "error": ""}
    return {
        "status": row.extract_status,
        "contract": _fresh_contract(row, chapter),
        "error": row.extract_error or "",
    }


def _record(
    db: Session, chapter: Chapter, chapter_text: str,
    status: str, contract: dict | None, error: str,
) -> None:
    """落一行契约记录(调用前已 purge 旧行;不 commit,由调用方随事务提交)。"""
    db.add(ChapterState(
        chapter_id=chapter.id,
        contract=json.dumps(contract, ensure_ascii=False) if contract else "",
        content_hash=content_hash(chapter_text),
        extract_status=status,
        extract_error=error[:500],
    ))


async def extract_handoff_contract(
    db: Session, chapter: Chapter, chapter_number: int, chapter_text: str, adapter
) -> None:
    """提取本章章末交接契约并落库。失败落 failed 行留痕,绝不抛异常阻塞主流程。

    幂等:先 purge 本章旧契约再重新提取(对齐 extractor 的清旧账模式)——
    重写章节时旧契约不会残留;一章永远只有一条当前契约。

    adapter 由调用方按 Task.HANDOFF_EXTRACT 路由好再传入(便于测试统一 mock)。

    事务纪律(对齐 extractor.extract_and_apply):入口 commit 丢掉调用方遗留的
    读快照;purge(写)后立即 commit,不拿写锁跨 LLM 调用;结果写入后再 commit。
    """
    db.commit()  # 丢掉遗留读快照,其后第一条 purge 写不会撞 SQLITE_BUSY

    # 清旧账:一章一条当前契约,重写即覆盖(旧版正文由 chapter_versions 留痕)
    old = db.query(ChapterState).filter(ChapterState.chapter_id == chapter.id).first()
    if old is not None:
        db.delete(old)
        db.flush()
    db.commit()  # 清账落盘 + 释放快照,LLM 调用期间无锁

    prompt = HANDOFF_CONTRACT_PROMPT.format(
        chapter_number=chapter_number,
        chapter_text=chapter_text[:_CONTRACT_TEXT_CHARS],
    )
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 契约失败不阻塞章节生成
        logger.error("第 %d 章契约提取调用失败: %s", chapter_number, exc)
        _record(db, chapter, chapter_text, "failed", None, f"LLM 调用失败:{exc}")
        db.commit()
        return

    contract = validate_contract(parse_llm_json(raw))
    if contract is None:
        logger.warning("第 %d 章契约 JSON 解析/结构校验失败,已留痕", chapter_number)
        _record(db, chapter, chapter_text, "failed", None, "契约 JSON 解析或结构校验失败")
        db.commit()
        return

    _record(db, chapter, chapter_text, "ok", contract, "")
    db.commit()
    logger.info(
        "第 %d 章契约提取完成:%s·%s·%d 人",
        chapter_number,
        contract.get("in_story_time") or "时间未知",
        contract.get("location") or "地点未知",
        len(contract["characters"]),
    )
