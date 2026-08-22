# app/engines/devices.py
# -*- coding: utf-8 -*-
"""常驻装置复现驱动(长程一致性 Phase 3:主动修「女主有系统却多章消失」)。

病根:故事宪法 canon.devices 把「系统/读心术/那枚玉佩」声明成了常驻装置,Phase 1
已让这份声明全程注入生成与门禁 —— 但那只是**被动**的一句「不可无故消失」。没人统计
它到底多少章没露面,也没人在动笔前催它上场,于是模型照旧写着写着就把金手指忘了。
伏笔调度器(consistency/foreshadow.py)管的是一次性叙事债 plant→payoff,天生不管
「反复出现的义务」,补不上这个洞。

本模块补的正是那口气:**记录 → 派生断档 → 催场 → 事后软报**,四步全程零 LLM 算术。

1. 记录:章末契约新增 devices_present(本章真正出场的装置名,见
   prompts/consistency.HANDOFF_CONTRACT_PROMPT;提取时按 devices_roster_block
   给的闭集清单认领,不许自创);
2. 派生:device_states 聚合各章契约,算出每个装置「上次出现在第几章、此后又过了
   几章」(不新增存储,仿 timeline.py 的读时聚合范式);
3. 催场:devices_reminder_block 在草稿 prompt 里点名逾期装置,按 importance 定阈值;
4. 软报:check_device_gaps / persist_device_issues 在定稿后落 advisory 建议
   (source="devices",封顶 major 不阻断)——催了还是没出现才报,是催场的兜底闭环。

降级纪律(与 Phase 2 时钟一致):**没在任何一章出现过的装置一律不催不报**。它可能
本就设定在后面章节才觉醒/入手,「从未出现」与「出现过又消失」是两件事;老书契约里
根本没有 devices_present,故全部装置都落在「从未出现」→ 整个模块静默,行为不劣于现状。
待批量补提契约或续写新章后,数据自然长出来。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState, Project
from app.engines.pipeline.handoff import _fresh_contract

# 断档几章开始催场:按装置重要度分档(critical 多为「每章都应有存在感」的金手指)
_REMIND_AFTER = {"critical": 2, "major": 3, "minor": 5}


def _norm(name: str) -> str:
    """归一装置名用于匹配:去掉所有空白。"""
    return "".join((name or "").split())


def _matches(device_name: str, reported: str) -> bool:
    """装置名与契约里认领的名字是否指同一个东西。

    提取 prompt 已要求「从清单里逐字挑」,但 LLM 常有细微出入(带书名号、加后缀)。
    三级宽容(仿 foreshadow._find_by_description):去空白精确 → 互为子串。
    子串需 ≥2 字,免得「玉」命中一切。
    """
    a, b = _norm(device_name), _norm(reported)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 2 and len(b) >= 2 and (a in b or b in a)


def _tracked_chapters(db: Session, project_id: int, upto: int) -> list[tuple[int, list[str]]]:
    """有效契约章的 (章号, 本章出场装置名) 升序表;upto 为**不含**上界。

    只收有效契约(提取成功 + 指纹对应当前正文)——无契约的章是「无从得知」,
    绝不能当成「装置没出现」来累计断档,否则老书会被误报一片。
    """
    chapters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number < upto,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    out: list[tuple[int, list[str]]] = []
    for ch in chapters:
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        contract = _fresh_contract(row, ch)
        if contract is None:
            continue
        present = contract.get("devices_present")
        out.append((ch.chapter_number, [str(x) for x in present] if isinstance(present, list) else []))
    return out


def device_states(db: Session, project_id: int, upto: int) -> list[dict]:
    """每个 canon 常驻装置的复现状态(派生,不新增存储);upto 为**不含**上界。

    返回 [{name, cadence, importance, last_seen, gap, threshold, overdue}],
    其中 gap = 上次出现之后又过了多少个**有契约的**章;last_seen 为 None 表示
    「在已统计的章里从未出现」→ gap 也为 None、overdue 恒 False(不催不报,
    它可能设定在后文才登场)。canon 无装置 → 空列表。
    """
    project = db.get(Project, project_id)
    if project is None:
        return []
    from app.schemas.canon import coerce_canon  # 懒导入:engines 不在顶层拉 schemas

    devices = [d for d in coerce_canon(project.canon).devices if d.name.strip()]
    if not devices:
        return []

    tracked = _tracked_chapters(db, project_id, upto)
    states: list[dict] = []
    for d in devices:
        name = d.name.strip()
        last_seen: int | None = None
        for chapter_number, present in tracked:
            if any(_matches(name, r) for r in present):
                last_seen = chapter_number
        gap = (
            sum(1 for chapter_number, _ in tracked if chapter_number > last_seen)
            if last_seen is not None else None
        )
        threshold = _REMIND_AFTER.get(d.importance, _REMIND_AFTER["major"])
        states.append({
            "name": name,
            "cadence": d.cadence.strip(),
            "importance": d.importance,
            "last_seen": last_seen,
            "gap": gap,
            "threshold": threshold,
            "overdue": gap is not None and gap >= threshold,
        })
    return states


def devices_reminder_block(db: Session, project_id: int, chapter_number: int) -> str:
    """草稿 prompt 的常驻装置催场块;无逾期装置 → 空串(零 token、零行为变化)。

    只点名「出现过又断档到阈值」的装置——canon 本身已由宪法块全程注入声明,
    这里不重复念清单,专治「该出场了」这一件事。
    """
    overdue = [s for s in device_states(db, project_id, chapter_number) if s["overdue"]]
    if not overdue:
        return ""
    lines = []
    for s in overdue:
        cad = f"·复现节奏:{s['cadence']}" if s["cadence"] else ""
        lines.append(
            f"- {s['name']}({s['importance']}{cad}):上次露面在第{s['last_seen']}章,"
            f"此后已有 {s['gap']} 章没它的戏"
        )
    return (
        "【常驻装置复现提醒(硬规则:已确立的常驻装置不能无故长期消失)】\n"
        + "\n".join(lines)
        + "\n本章必须让上面逾期的装置真正回到台面——要发挥实际作用(推动抉择、给出信息、"
        "带来代价),不是提一句名字充数,也不要硬塞一段与本章情节无关的展示。"
        "若本章确实无处安放,至少让主角对它有一次具体的动念或试图使用而未果,"
        "别让读者觉得这设定被作者忘了。"
    )


def devices_roster_block(project: Project | None) -> str:
    """契约提取 prompt 的装置清单块(闭集):让场记只认领已登记的装置,不自创。

    无 canon 装置 → 空串(提取 prompt 的 devices_present 自然填 [])。
    """
    if project is None:
        return ""
    from app.schemas.canon import coerce_canon

    names = [d.name.strip() for d in coerce_canon(project.canon).devices if d.name.strip()]
    if not names:
        return ""
    return (
        "\n【本书登记的常驻装置(devices_present 只能从这份清单里认领,不得自创)】\n"
        + "\n".join(f"- {n}" for n in names)
        + "\n"
    )


def check_device_gaps(states: list[dict], focus_chapter: int) -> list[dict]:
    """常驻装置断档的 advisory 校验(纯函数,不阻断)。

    states 需由 device_states(upto=focus_chapter+1) 算出(即已含本章战果)。
    催场阈值 threshold 之上再宽一章才报(gap >= threshold+1)——写第 N 章前催过了、
    本章仍没让它出场,才算真漏,不与提醒重复唠叨。
    severity:critical 装置 major,其余 minor;一律不到 blocker,不 quarantine。
    """
    issues: list[dict] = []
    for s in states:
        gap = s.get("gap")
        if gap is None or gap < s["threshold"] + 1:
            continue
        cad = f",约定复现节奏:{s['cadence']}" if s["cadence"] else ""
        issues.append({
            "severity": "major" if s["importance"] == "critical" else "minor",
            "type": "worldrule",
            "description": (
                f"常驻装置「{s['name']}」已连续 {gap} 章没有出现"
                f"(上次露面在第{s['last_seen']}章,截至第{focus_chapter}章末仍未回来)。"
                f"它是宪法里登记的常驻设定{cad},长期消失读者会觉得这金手指被作者忘了。"
            ),
            "suggestion": (
                f"在本章或紧接的下一章安排「{s['name']}」发挥一次实际作用(推动抉择/给出信息/"
                "带来代价);若剧情上它本该暂时失效或被封印,请在正文里把原因交代清楚,"
                "或到项目设定的故事宪法里调整它的复现节奏。"
            ),
        })
    return issues


def persist_device_issues(db: Session, project_id: int, chapter: Chapter, text: str) -> None:
    """定稿后跑常驻装置断档校验,把结果落成 advisory issue(source="devices")。

    读时聚合:device_states(upto=本章+1,含本章刚提交的契约)→ check_device_gaps。
    懒导入 checker.persist_issues 避免循环导入(checker 属 consistency 包,已拉一堆
    engines 模块)。自带 commit;无逾期装置也照常 persist(幂等清掉本章上一版 devices
    建议,装置补回来后旧告警自动消失)。canon 无装置 / 契约无数据 → states 为空,等价 no-op。

    调用方(apply_chapter_tail)在契约抽取之后调用并 try/except 自吞——纯算术无 LLM,
    但读聚合仍可能撞库,绝不能拖垮章后主链路。
    """
    from app.engines.consistency.checker import persist_issues  # 懒导入破循环

    states = device_states(db, project_id, chapter.chapter_number + 1)
    issues = check_device_gaps(states, chapter.chapter_number)
    persist_issues(db, chapter, issues, source="devices", text=text)
    db.commit()
