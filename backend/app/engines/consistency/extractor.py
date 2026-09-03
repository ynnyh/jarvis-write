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

from app.db.models import Chapter, Entity, Project
from app.engines.consistency.bible import BibleService
from app.engines.consistency.foreshadow import ForeshadowScheduler
from app.engines.consistency.motifs import apply_extraction, known_labels_block
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import EXTRACTION_PROMPT
from app.schemas.canon import StoryCanon, coerce_canon

logger = logging.getLogger("jarvis-write.extractor")

_MAX_OPEN_FS = 40  # 抽取提示里最多列出的未回收伏笔数(取最早埋设,防长篇膨胀)


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


def salvage_json_objects(text: str) -> list[dict]:
    """从(可能被截断的)输出里抢救出所有**完整**的叶子对象。

    截断不是罕见事故:推理模型的思考吃掉大半 max_tokens,正文常停在半个字符串上,
    `json.loads` 直接 Unterminated string,于是**整批结果全丢**——哪怕前面 3 条
    已经写完了。这里退一步:按花括号配平逐个切出对象,只有最后那个半截的丢掉。
    顺带也兼容「顶层是数组」和「围栏里混了解释文字」两种常见跑偏。

    只取**叶子对象**(内部不再嵌套对象):批量契约都是
    `{"key": [{扁平条目}, ...]}` 形状,取叶子恰好等于取数组元素,不会把
    外层包装对象也当成一条结果。
    """
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)(?:```|$)", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    out: list[dict] = []
    stack: list[list[int]] = []  # [起始下标, 内部已闭合的对象数]
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append([i, 0])
        elif ch == "}" and stack:
            start, children = stack.pop()
            if stack:
                stack[-1][1] += 1
            if children:
                continue  # 不是叶子:里头的条目已经单独收了
            try:
                obj = json.loads(s[start : i + 1])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    if out:
        logger.warning("JSON 抢救模式:从半截/异形输出里取出 %d 条完整对象", len(out))
    return out


def _coerce_int(raw: object, default: int = 0) -> int:
    """把 LLM 的 "31"/31/31.0/脏值收敛成非负 int;不可解析回落 default。"""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _build_canon_suggestion_issues(
    suggestions: object, existing: StoryCanon
) -> list[dict]:
    """把 LLM 的 canon_suggestions 转成 source=canon 的 advisory issue dict。

    咨询式:这些只是给作者过目的「建议」,绝不自动写进 project.canon。逐条 try 保护,
    坏形状跳过不抛;去重掉【已在现有宪法里】的(采纳后条目进 canon,下次抽取自然不再
    重复冒出,不依赖 issue 状态去重);总共最多 3 条(与 EXTRACTION_PROMPT 口径一致)。
    payload 存结构化提案,供「采纳进宪法」端点无损重建 canon 条目。
    """
    if not isinstance(suggestions, list):
        return []
    out: list[dict] = []
    seen_absence = {a.strip() for a in existing.absences if (a or "").strip()}
    seen_device = {d.name.strip() for d in existing.devices if d.name.strip()}
    has_deadline = existing.deadline is not None and bool(existing.deadline.name.strip())

    for raw in suggestions:
        if len(out) >= 3:
            break
        try:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "").strip().lower()
            evidence = str(raw.get("evidence") or "").strip()
            reason = str(raw.get("reason") or "").strip()

            if kind == "absence":
                txt = str(raw.get("text") or "").strip()
                if not txt or txt in seen_absence:
                    continue
                seen_absence.add(txt)
                out.append({
                    "severity": "minor", "type": "absence",
                    "description": f"建议加入刻意留白:{txt}",
                    "evidence": evidence,
                    "suggestion": reason or "采纳后写入故事宪法,全程注入生成并参与一致性门禁。",
                    "payload": {"kind": "absence", "text": txt},
                })
            elif kind == "device":
                name = str(raw.get("name") or "").strip()
                if not name or name in seen_device:
                    continue
                seen_device.add(name)
                cadence = str(raw.get("cadence") or "").strip()
                importance = str(raw.get("importance") or "").strip().lower()
                if importance not in ("critical", "major", "minor"):
                    importance = "major"
                desc = f"建议加入常驻装置:{name}"
                if cadence:
                    desc += f"(复现节奏:{cadence})"
                out.append({
                    "severity": "minor", "type": "device",
                    "description": desc,
                    "evidence": evidence,
                    "suggestion": reason or "采纳后作为常驻装置全程注入,门禁会盯它别无故长期消失。",
                    "payload": {
                        "kind": "device", "name": name,
                        "cadence": cadence, "importance": importance,
                    },
                })
            elif kind == "deadline":
                if has_deadline:
                    continue  # 全书只设一个倒计时,已有则不再提议
                name = str(raw.get("name") or "").strip()
                if not name:
                    continue
                has_deadline = True  # 同批多条也只取第一个
                total_days = _coerce_int(raw.get("total_days"))
                anchor = _coerce_int(raw.get("anchor_chapter"), default=1) or 1
                days_txt = f",共 {total_days} 天" if total_days > 0 else ""
                out.append({
                    "severity": "minor", "type": "deadline",
                    "description": f"建议加入倒计时:{name}{days_txt},自第 {anchor} 章起算",
                    "evidence": evidence,
                    "suggestion": reason or "采纳后作为权威时间锚,门禁会校验各章天数与剩余时间一致。",
                    "payload": {
                        "kind": "deadline", "name": name,
                        "total_days": total_days, "anchor_chapter": anchor,
                    },
                })
        except Exception:  # noqa: BLE001 — 单条坏形状跳过,不拖累其它建议
            continue
    return out


def _persist_canon_suggestions(
    db: Session, project_id: int, chapter_number: int,
    chapter_text: str, suggestions: object,
) -> int:
    """把本章 canon 建议落成 source=canon 的 advisory ChapterIssue —— 绝不改 project.canon。

    独立于核心圣经提交【之后】单跑:全程无 await(无「写锁跨 LLM 调用」并发风险),
    读现有 canon 去重后经 persist_issues 幂等重建本章 source=canon 记录并单独提交。
    返回落库条数;无可提议时返回 0(不空跑提交)。
    """
    # 延迟导入:checker 顶部已 import 本模块 parse_llm_json,反向顶层 import 会循环。
    from app.engines.consistency.checker import persist_issues

    project = db.get(Project, project_id)
    if project is None:
        return 0
    issues = _build_canon_suggestion_issues(suggestions, coerce_canon(project.canon))
    if not issues:
        return 0
    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if chapter is None:
        return 0
    persist_issues(db, chapter, issues, source="canon", text=chapter_text)
    db.commit()
    return len(issues)


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
    _open = scheduler.open_foreshadowings()
    open_fs = "\n".join(
        f"- {f.description}(第{f.chapter_planted}章埋设)"
        for f in _open[:_MAX_OPEN_FS]
    ) or "(暂无)"
    if len(_open) > _MAX_OPEN_FS:
        open_fs += f"\n- …(另有 {len(_open) - _MAX_OPEN_FS} 条未回收伏笔未列出)"
    # 桥段台账已有标签(排除本章):供 LLM 同物同名,跨章聚合才数得准
    known_motifs = known_labels_block(db, project_id, exclude_chapter=chapter_number)
    prompt = EXTRACTION_PROMPT.format(
        known_entities=known_entities,
        active_facts=active_facts,
        open_foreshadowings=open_fs,
        known_motifs=known_motifs or "(暂无)",
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
    # 桥段台账(防跨章复读):与圣经同一事务落库,重写本章时随 purge 幂等重建
    motif_stats = apply_extraction(
        db, project_id, chapter_number, extraction.get("motifs") or []
    )
    db.commit()
    stats = {
        "bible": bible_stats, "foreshadow": fs_stats,
        "motifs": motif_stats, "purged": purge_stats,
    }

    # 4. canon 建议(咨询式增值,不自动落库):核心圣经已原子提交,这里【之后】独立单跑。
    #    失败只回滚本段、记日志,绝不影响已落地的圣经/伏笔,也绝不拖垮章节生成
    #    (chapter.py 未包 extract_and_apply,抛出即崩生成 —— 故必须自吞异常)。
    try:
        n = _persist_canon_suggestions(
            db, project_id, chapter_number, chapter_text,
            extraction.get("canon_suggestions") or [],
        )
        if n:
            stats["canon_suggestions"] = n
    except Exception as exc:  # noqa: BLE001 — 建议是增值项,绝不拖垮抽取/生成
        db.rollback()  # 仅回滚本段未提交的 canon 写;核心圣经已 commit,不受影响
        logger.warning("canon 建议落库失败(不影响圣经): %s", exc)

    return stats
