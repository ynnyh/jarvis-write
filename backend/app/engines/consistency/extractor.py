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

    幂等:先撤销该章此前的抽取记录再重抽——重写正文不会污染圣经。

    事务纪律(WAL 下防 database is locked):本函数自洽管理提交,不依赖调用方——
    生成/拆章/re-extract 三个调用方一处修好。
      0) 入口先 commit,丢掉调用方可能遗留的读快照(如 check_chapter 读圣经又发 LLM
         却没提交),否则下面第一条 purge 写会拿过期快照升级写锁 → 撞 SQLITE_BUSY
         (WAL 下该错误不走 busy_timeout,直接报 database is locked)。
      1) purge(写)+ 读取抽取提示输入(读)后 commit:既持久化清账,又释放读快照,
         不把写锁/快照带进随后的 LLM 调用(否则并发写全被堵到 LLM 时长)。
      2) LLM 调用期间无锁无快照(用量记账在别的连接提交也不会撞我们)。
      3) apply(写)后 commit。
    三步都幂等,故调用方可在遇锁时对本函数整体重试。
    """
    bible = BibleService(db, project_id)
    scheduler = ForeshadowScheduler(db, project_id)

    # 0. 丢掉调用方遗留的读快照,其后第一条 purge 写才不会撞 SQLITE_BUSY。
    #    (expire_on_commit=False,提交不会让调用方已持有的 ORM 对象过期。)
    db.commit()

    # 1. 清旧账(写)+ 组装抽取提示(读),然后提交:持久化清账并释放读快照。
    #    防记忆污染:清掉本章旧账(首次抽取时无旧账,清理为空操作)。
    purge_stats = {
        "bible": bible.purge_chapter_extraction(chapter_number),
        "foreshadow": scheduler.purge_chapter_ops(chapter_number),
    }

    # 已退场实体不再注入抽取提示:不再为退场人物累积新事实
    known_entities = "\n".join(
        f"- {e.name}({e.entity_type})"
        for e in db.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.retired.is_(False),
        )
    ) or "(暂无)"

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
    db.commit()  # ← 关键:LLM 前提交,别拿着写锁 + 读快照跨 LLM 调用

    # 2. LLM(此刻无锁无快照)
    try:
        raw = await get_adapter_for(Task.FACT_EXTRACT).ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不阻塞章节生成
        logger.error("抽取调用失败: %s", exc)
        return {}

    extraction = parse_llm_json(raw)
    if not extraction:
        return {}

    # 3. 应用(写)→ 提交
    bible_stats = bible.apply_extraction(chapter_number, extraction)
    fs_stats = scheduler.apply_ops(
        chapter_number, extraction.get("foreshadow_ops") or []
    )
    db.commit()
    return {"bible": bible_stats, "foreshadow": fs_stats, "purged": purge_stats}
