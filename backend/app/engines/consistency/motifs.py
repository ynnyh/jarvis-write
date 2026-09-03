# app/engines/consistency/motifs.py
# -*- coding: utf-8 -*-
"""桥段台账 + 雷区清单:跨章「同一描写反复出现」的语义级防复读。

病根:模型生成第 N 章时只看得到滚动摘要 + 前 2 章结尾,中间几章的叙事内容
完全不可见,于是把前文写过的招牌意象(铁锈玫瑰)、标志性动作(扎手/扎胸膛)、
场景收束套路(躺下等天亮)换个措辞再写一遍。n-gram 查重(repetition.py)只抓
字面重复,抓不住这类「同桥段换写法」;批注又是单章段落的临时改稿,跨不了章。

本模块补两层(与 repetition.py 字面查重互补,勿混为一谈):
  1. 桥段台账(自动):章后抽取顺带把本章最显眼的描写母题沉淀成短标签
     (铁锈玫瑰/扎胸膛/躺下等天亮),按标签跨章聚合出现章号与次数;写第 N 章
     前把「已出现 ≥2 次的母题」连同章号注入草稿 prompt——次数越多措辞越硬。
  2. 雷区清单(作者明令):用户手动登记「这本书别再写 X」,一次标注全书生效,
     注入草稿/定稿/守卫/去味全链路(style_block),并作为事后软报的对照源。

纪律:
  - 台账只回流「标签 + 章号 + 次数」,绝不沉淀原句——原句进 prompt 会被逐字
    照抄(style_memo 同一条铁律);
  - 雷区/台账的软报(source="repeat")一律 advisory,不到 blocker——重复描写
    是质量问题不是事实矛盾,拦停只会烧回炉预算。
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Chapter, Project, WritingMotif

logger = logging.getLogger("jarvis-write.motifs")

# 渲染进 prompt 的台账条数上限(按次数降序取最滥的几条)
_MAX_LEDGER_LINES = 8
# 抽取/扫描 prompt 里展示的已有标签数上限(供 LLM 逐字复用标签,防同物异名)
_MAX_KNOWN_LABELS = 20
# 事后软报单章最多几条(雷区 + 台账合计),防一章刷屏
_MAX_REPEAT_ISSUES = 6
# 全书扫描:一次 LLM 调用喂几章(每章截断后约 4 千字,4 章一次调用可控)
_SCAN_BATCH = 4
_SCAN_CHAPTER_CHARS = 4000


def _norm_label(label: str) -> str:
    """标签归一:去空白。聚合与唯一性都按归一后比对。"""
    return "".join((label or "").split())


# ---------------------------------------------------------------------------
# 雷区清单(作者明令,(project, label) 唯一)
# ---------------------------------------------------------------------------

def add_banned(db: Session, project_id: int, label: str, detail: str = "") -> WritingMotif:
    """登记/更新一条雷区:同标签已存在则只更新说明,幂等。"""
    key = _norm_label(label)
    if len(key) < 2:
        raise ValueError("雷区标签至少 2 个字")
    row = (
        db.query(WritingMotif)
        .filter(
            WritingMotif.project_id == project_id,
            WritingMotif.banned.is_(True),
        )
        .all()
    )
    for r in row:
        if _norm_label(r.label) == key:
            if detail.strip() and detail.strip() != r.detail:
                r.detail = detail.strip()
                db.flush()
            return r
    motif = WritingMotif(
        project_id=project_id, label=key, detail=detail.strip(),
        chapter_number=0, source="user", banned=True,
    )
    db.add(motif)
    db.flush()
    return motif


def remove_banned(db: Session, project_id: int, motif_id: int) -> bool:
    """撤销一条雷区;台账历史(同标签的 auto 行)不受影响。"""
    row = (
        db.query(WritingMotif)
        .filter(
            WritingMotif.id == motif_id,
            WritingMotif.project_id == project_id,
            WritingMotif.banned.is_(True),
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def banned_rows(db: Session, project_id: int) -> list[WritingMotif]:
    """全部雷区,按登记时间先后。"""
    return (
        db.query(WritingMotif)
        .filter(WritingMotif.project_id == project_id, WritingMotif.banned.is_(True))
        .order_by(WritingMotif.id)
        .all()
    )


def promote_to_banned(db: Session, project_id: int, label: str) -> WritingMotif | None:
    """把台账里的既有标签升格为雷区:说明取最近一次 auto 行的 detail。

    台账里没有这个标签时返回 None(不凭空造雷区——防前端传错字静默生效)。
    """
    key = _norm_label(label)
    if len(key) < 2:
        return None
    autos = (
        db.query(WritingMotif)
        .filter(
            WritingMotif.project_id == project_id,
            WritingMotif.banned.is_(False),
        )
        .order_by(WritingMotif.chapter_number.desc(), WritingMotif.id.desc())
        .all()
    )
    matching = [r for r in autos if _norm_label(r.label) == key]
    if not matching:
        return None
    return add_banned(db, project_id, key, next((r.detail for r in matching if r.detail), ""))


# ---------------------------------------------------------------------------
# 台账(自动:章后抽取 / 全书扫描;每 (project, chapter, label) 一行)
# ---------------------------------------------------------------------------

def purge_chapter(db: Session, project_id: int, chapter_number: int) -> int:
    """清掉某章的全部台账行(重写/重抽幂等的前半步)。返回删除行数。"""
    olds = (
        db.query(WritingMotif)
        .filter(
            WritingMotif.project_id == project_id,
            WritingMotif.banned.is_(False),
            WritingMotif.chapter_number == chapter_number,
        )
        .all()
    )
    for r in olds:
        db.delete(r)
    db.flush()
    return len(olds)


def apply_extraction(
    db: Session, project_id: int, chapter_number: int, motifs: list[dict]
) -> int:
    """章后抽取/扫描的落库:清本章旧账 → 写入去重后的标签。返回写入行数。

    单章内同标签(归一后)只留第一条;标签不足 2 字的脏值丢弃。
    不 commit——与圣经抽取同事务原子提交(extractor 的纪律)。
    """
    purge_chapter(db, project_id, chapter_number)
    seen: set[str] = set()
    added = 0
    for m in motifs:
        if not isinstance(m, dict):
            continue
        key = _norm_label(str(m.get("label") or ""))
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        db.add(WritingMotif(
            project_id=project_id,
            label=key,
            detail=str(m.get("detail") or "").strip(),
            chapter_number=chapter_number,
            source="auto",
            banned=False,
        ))
        added += 1
    db.flush()
    return added


def ledger(db: Session, project_id: int, upto: int | None = None) -> list[dict]:
    """聚合台账:标签 → 出现章号列表 + 次数,按次数降序。

    upto(不含上界)只统计该章之前的行——生成第 N 章时看 N 之前的账。
    雷区行不算在台账里(它们走更强的禁令块,不重复占预算)。
    """
    q = db.query(WritingMotif).filter(
        WritingMotif.project_id == project_id, WritingMotif.banned.is_(False)
    )
    if upto is not None:
        q = q.filter(WritingMotif.chapter_number < upto)
    agg: dict[str, dict] = {}
    for r in q.all():
        if r.chapter_number <= 0:
            continue
        item = agg.setdefault(_norm_label(r.label), {
            "label": _norm_label(r.label), "detail": "", "chapters": [],
        })
        if not item["detail"] and r.detail:
            item["detail"] = r.detail
        if r.chapter_number not in item["chapters"]:
            item["chapters"].append(r.chapter_number)
    out = sorted(
        ({"label": v["label"], "detail": v["detail"], "chapters": sorted(v["chapters"]),
          "count": len(v["chapters"])} for v in agg.values()),
        key=lambda x: (-x["count"], -max(x["chapters"])),
    )
    return out


# ---------------------------------------------------------------------------
# 注入块(生成 prompt 用)
# ---------------------------------------------------------------------------

def known_labels_block(db: Session, project_id: int, exclude_chapter: int | None = None) -> str:
    """抽取/扫描 prompt 的「已有标签」清单:让 LLM 同物同名,聚合才数得起来。

    无台账 → 空串(开篇零影响)。
    """
    labels = [
        it["label"] for it in ledger(db, project_id, upto=exclude_chapter)
        if it["count"] >= 1
    ][:_MAX_KNOWN_LABELS]
    if not labels:
        return ""
    return "、".join(labels)


def banned_block(db: Session, project_id: int) -> str:
    """雷区块:注入 style_block,草稿/定稿/守卫压缩/去味重写全链路可见。

    无雷区 → 空串(零 token、零行为变化)。
    """
    rows = banned_rows(db, project_id)
    if not rows:
        return ""
    lines = [f"- {r.label}" + (f":{r.detail}" if r.detail else "") for r in rows]
    return (
        "\n【本书雷区(作者明令禁止,硬规则)】以下桥段/意象/写法已被作者点名禁用,"
        "本章及今后各章一律不得再出现,换个措辞、换个载体也算违禁;需要类似效果时,"
        "必须另创全新的意象或动作:\n" + "\n".join(lines) + "\n"
    )


def ledger_avoid_block(db: Session, project_id: int, chapter_number: int) -> str:
    """台账规避块:只列已出现 ≥2 次的母题,按次数升措辞强度,拼进 {avoid_repetition}。

    出现 ≥3 次 = 已写滥,本章禁止再用;2 次 = 已显重复,换写法或干脆避开。
    已进雷区的标签不再列(更强的禁令块已覆盖)。空台账 → 空串。
    """
    banned_keys = {_norm_label(r.label) for r in banned_rows(db, project_id)}
    items = [
        it for it in ledger(db, project_id, upto=chapter_number)
        if it["count"] >= 2 and it["label"] not in banned_keys
    ][:_MAX_LEDGER_LINES]
    if not items:
        return ""
    lines = []
    for it in items:
        chs = "、".join(f"第{c}章" for c in it["chapters"])
        verdict = (
            "已写滥,本章禁止再用这个意象/桥段,连近似变体也不要"
            if it["count"] >= 3
            else "已显重复,本章要么不用,要么换一种完全不同的写法"
        )
        lines.append(
            f"- {it['label']}({chs},共{it['count']}次)"
            + (f":{it['detail']}" if it["detail"] else "")
            + f"——{verdict}"
        )
    return (
        "【跨章桥段台账(前文已反复出现的描写,防复用)】\n" + "\n".join(lines)
        + "\n写新章要创造新意象、新动作、新收束方式,不要回收上面这些旧母题;"
        "也不要只换个措辞复用(那和照抄一样难看)。"
    )


# ---------------------------------------------------------------------------
# 事后软报(advisory,source="repeat"):终稿里又出现雷区/写滥母题时亮出来
# ---------------------------------------------------------------------------

def _evidence_around(text: str, label: str, width: int = 60) -> str:
    """取 label 首次出现处附近的原文片段作证据(前后各约 width/2 字)。"""
    idx = text.find(label)
    if idx < 0:
        return ""
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(label) + width // 2)
    frag = text[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")


def check_motif_repeats(
    db: Session, project_id: int, chapter_number: int, text: str
) -> list[dict]:
    """终稿对照雷区与台账的确定性软报(纯 contains,零 LLM)。

    - 雷区标签命中 → major(作者明令,又写出来必须处理);
    - 台账标签(此前章已出现 ≥2 次)本章再现 → minor(第 3 次以上的信号)。
    一律不到 blocker;条数封顶 _MAX_REPEAT_ISSUES。空表 → 空列表。
    """
    if not text.strip():
        return []
    flat = "".join(text.split())  # 归一正文:命中判定不受换行/空格干扰
    issues: list[dict] = []
    for r in banned_rows(db, project_id):
        if len(issues) >= _MAX_REPEAT_ISSUES:
            break
        if _norm_label(r.label) not in flat:
            continue
        issues.append({
            "severity": "major",
            "type": "repetition",
            "description": f"本章又出现了雷区「{r.label}」(作者已明令全书禁用)",
            "evidence": _evidence_around(text, r.label),
            "suggestion": (
                f"把这一处改写成与「{r.label}」无关的全新意象/动作;"
                "若确需保留,请先到「全书 → 桥段」撤销这条雷区。"
            ),
        })
    banned_keys = {_norm_label(r.label) for r in banned_rows(db, project_id)}
    for it in ledger(db, project_id, upto=chapter_number):
        if len(issues) >= _MAX_REPEAT_ISSUES:
            break
        if it["count"] < 2 or it["label"] in banned_keys:
            continue
        if it["label"] not in flat:
            continue
        chs = "、".join(f"第{c}章" for c in it["chapters"])
        issues.append({
            "severity": "minor",
            "type": "repetition",
            "description": (
                f"「{it['label']}」此前已在{chs}写过,本章再次出现(第{it['count'] + 1}次)"
            ),
            "evidence": _evidence_around(text, it["label"]),
            "suggestion": (
                f"同一个母题反复出现读者会疲劳,建议改写成全新意象;若这是有意的主母题"
                f"复现,可在「全书 → 桥段」把它清除出台账,或设为雷区以外的常驻意象。"
            ),
        })
    return issues


def persist_motif_issues(db: Session, project_id: int, chapter: Chapter, text: str) -> None:
    """终稿软报落库(source="repeat",advisory),幂等;自带 commit。

    与 devices/clock 的章后软报同范式:persist_issues 幂等重建本章该来源记录,
    正文改过重跑时旧告警自动消失。调用方 apply_chapter_tail 需 try/except 自吞
    ——纯算术,但读聚合仍可能撞库,绝不拖垮章后主链路。
    """
    from app.engines.consistency.checker import persist_issues  # 懒导入破循环

    issues = check_motif_repeats(db, project_id, chapter.chapter_number, text)
    persist_issues(db, chapter, issues, source="repeat", text=text)
    db.commit()


# ---------------------------------------------------------------------------
# 全书扫描(存量书回填台账;显式触发,批喂 LLM)
# ---------------------------------------------------------------------------

async def scan_book_motifs(
    db: Session, project_id: int, progress=None
) -> dict:
    """扫描全部已成文章,逐批抽取描写母题回填台账(幂等:每章先清后插)。

    老书没有台账数据,防复浊从下一章才开始——本扫描一次性把历史章节的母题
    补齐,台账立即可用。批间提交(别拿写事务跨 LLM 调用);雷区行不受影响。
    返回 {chapters_scanned, motifs_added}。
    """
    from app.llm.router import Task, get_adapter_for
    from app.prompts.consistency import MOTIF_SCAN_PROMPT
    from app.engines.consistency.extractor import parse_llm_json

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响扫描
                pass

    chapters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    if not chapters:
        return {"chapters_scanned": 0, "motifs_added": 0}

    known = known_labels_block(db, project_id)
    scanned = 0
    added = 0
    for i in range(0, len(chapters), _SCAN_BATCH):
        batch = chapters[i : i + _SCAN_BATCH]
        corpus = "\n\n".join(
            f"【第{c.chapter_number}章】\n{c.final_content[:_SCAN_CHAPTER_CHARS]}"
            for c in batch
        )
        _report(f"扫描母题:{i + 1}-{min(i + _SCAN_BATCH, len(chapters))}/{len(chapters)} 章")
        # 读完即提交:LLM 调用期间不持读快照(WAL 下防 SQLITE_BUSY 的老纪律)
        db.commit()
        try:
            raw = await get_adapter_for(Task.FACT_EXTRACT).ask(
                MOTIF_SCAN_PROMPT.format(known_labels=known or "(暂无)", chapters_text=corpus)
            )
        except Exception as exc:  # noqa: BLE001 — 单批失败跳过,不拖垮整次扫描
            logger.warning("桥段扫描第 %s 批调用失败(跳过): %s", i // _SCAN_BATCH + 1, exc)
            continue
        parsed = parse_llm_json(raw)
        rows = parsed.get("chapters") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            continue
        by_num = {c.chapter_number: c for c in batch}
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            n = entry.get("chapter_number")
            motifs = entry.get("motifs")
            if not isinstance(n, int) or n not in by_num or not isinstance(motifs, list):
                continue
            added += apply_extraction(db, project_id, n, motifs)
            scanned += 1
        db.commit()
        # 扫出的新标签回流给下一批,全程同物同名
        known = known_labels_block(db, project_id) or known
    logger.info("桥段扫描完成:%d 章,新增台账 %d 条", scanned, added)
    return {"chapters_scanned": scanned, "motifs_added": added}
