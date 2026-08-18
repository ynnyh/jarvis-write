# app/engines/consistency/extractor.py
# -*- coding: utf-8 -*-
"""章后状态抽取器:LLM 从正文抽取持久状态变化 → 写回圣经与伏笔表。

这是让圣经"活起来"的闭环:不抽取,圣经就是死数据。
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Entity
from app.engines.consistency.bible import BibleService
from app.engines.consistency.foreshadow import ForeshadowScheduler
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import EXTRACTION_PROMPT

logger = logging.getLogger("jarvis-write.extractor")


def parse_llm_json(text: str) -> dict:
    """宽容解析 LLM 输出的 JSON:剥 markdown 围栏、截取首尾大括号。"""
    text = text.strip()
    # 剥 ```json ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 截取最外层大括号
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM JSON 解析失败: %s;原文前200字: %s", exc, text[:200])
        return {}


async def extract_and_apply(
    db: Session, project_id: int, chapter_number: int, chapter_text: str
) -> dict:
    """跑一次抽取并写库。返回统计;抽取失败返回空统计,不阻塞主流程。

    幂等:抽取成功后先撤销该章此前的抽取记录再重抽——重写正文不会污染圣经。

    事务纪律(WAL 下防 database is locked + 防数据丢失):本函数自洽管理提交,不依赖
    调用方——生成/拆章/re-extract 三个调用方一处修好。
      0) 入口先 commit,丢掉调用方可能遗留的读快照,否则随后的写会拿过期快照升级写锁
         → 撞 SQLITE_BUSY(WAL 下不走 busy_timeout,直接报 database is locked)。
      1) 预演清账:purge(写)+ 读抽取提示输入(读)渲染出提示文本后立即 rollback。
         提示需要「本章之前」的状态视图,故必须先 purge 再渲染;但清账绝不落盘——
         rollback 既还原旧账(LLM 失败不丢数据),又释放读快照(不带进 LLM 调用)。
      2) LLM 调用期间无锁无快照;失败/空返回时旧账仍在,直接返回。
      3) 抽取成功才真正 purge(写)+ apply(写),同一事务原子 commit,无中间空窗。
    全程幂等,故调用方可在遇锁时对本函数整体重试。
    """
    bible = BibleService(db, project_id)
    scheduler = ForeshadowScheduler(db, project_id)

    # 0. 丢掉调用方遗留的读快照,其后的写才不会撞 SQLITE_BUSY。
    #    (expire_on_commit=False,提交不会让调用方已持有的 ORM 对象过期。)
    db.commit()

    # 1. 预演清账 → 渲染提示 → 回滚。提示需要「本章之前」的状态视图(供 LLM 判断本章
    #    改了什么),故必须先 purge 再渲染;但清账绝不在 LLM 前落盘 —— 老版把 purge
    #    提交在 LLM 之前,一旦 LLM 失败/空返回,本章旧账已被永久清空且无新值替补
    #    (再抽取场景数据丢失)。预演后 rollback 还原旧账,LLM 失败也毫发无损。
    known_entities = "\n".join(
        f"- {e.name}({e.entity_type})"
        for e in db.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.retired.is_(False),
        )
    ) or "(暂无)"  # 已退场实体不注入:不再为退场人物累积新事实
    bible.purge_chapter_extraction(chapter_number)
    scheduler.purge_chapter_ops(chapter_number)
    active_facts = bible.hard_constraints_block(chapter_number)
    open_fs = "\n".join(
        f"- {f.description}(第{f.chapter_planted}章埋设)"
        for f in scheduler.open_foreshadowings()
    ) or "(暂无)"
    prompt = EXTRACTION_PROMPT.format(
        known_entities=known_entities,
        active_facts=active_facts,
        open_foreshadowings=open_fs,
        chapter_number=chapter_number,
        chapter_text=chapter_text[:12000],  # 防超长
    )
    # 预演结束:回滚清账,库回到抽取前原样,同时释放读快照 —— LLM 前无锁无快照。
    db.rollback()

    # 2. LLM(此刻无锁无快照;失败/空返回时旧账仍在,直接返回不损数据)
    try:
        raw = await get_adapter_for(Task.FACT_EXTRACT).ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不阻塞章节生成
        logger.error("抽取调用失败: %s", exc)
        return {}

    extraction = parse_llm_json(raw)
    if not extraction:
        return {}

    # 3. 抽取成功,才真正清账 + 应用:purge 与新值在同一事务,无中间空窗,末尾原子提交。
    purge_stats = {
        "bible": bible.purge_chapter_extraction(chapter_number),
        "foreshadow": scheduler.purge_chapter_ops(chapter_number),
    }
    bible_stats = bible.apply_extraction(chapter_number, extraction)
    fs_stats = scheduler.apply_ops(
        chapter_number, extraction.get("foreshadow_ops") or []
    )
    db.commit()
    return {"bible": bible_stats, "foreshadow": fs_stats, "purged": purge_stats}
