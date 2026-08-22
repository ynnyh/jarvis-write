# app/engines/timeline.py
# -*- coding: utf-8 -*-
"""全书剧情时间线 + 结构化故事时钟(docs/08 §7 P2-⑨ 轻量落地:不独立建表,从契约聚合)。

设计取舍:契约(chapter_states)里已有各章章末的剧情时间/地点/时间跳跃提示,
再建一张 LLM 时间线表是重复真相源——这里零 LLM 直接从有效契约(提取成功
且指纹对应当前正文)聚合。

两层能力:
1. 时间线(book_timeline / timeline_block):各章章末剧情时间/地点/故事天数,
   写前预审与写后门禁在"相邻两章"之外再看到全书走向,抓跨章时间倒流/跳跃盲区;
2. 故事时钟(compute_clock / check_story_clock):以各章契约 story_day + 故事宪法
   canon.deadline 为唯一真相源,算出权威天数轴与倒计时应剩天数——
   - timeline_block 把权威剩余天数喂进门禁 prompt(LLM 阻断路径:只做比对不做算术);
   - check_story_clock 定稿后跑确定性算术,把矛盾落成 advisory 建议(source=clock,
     不阻断——它在门禁之后跑,且 LLM 抽 story_day 不可靠,只做提示)。

契约缺失的老章节自然跳过(时间线断档 / 时钟无数据),批量补提契约后自动补齐;
无倒计时定义或 story_day 稀疏时,时钟算术一律降级为空,行为不劣于现状。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState, Project
from app.engines.pipeline.handoff import _fresh_contract

# prompt 注入只带最近若干条,防长书 token 膨胀(相邻对照已有上章契约兜底)
_PROMPT_MAX_ENTRIES = 15


def book_timeline(db: Session, project_id: int) -> list[dict]:
    """全书剧情时间线:各章章末的剧情时间/地点/故事天数/跳跃提示,按章号升序。

    只收有效契约(提取成功 + 指纹对应当前正文);无契约/失效的章跳过。
    story_day/days_remaining 为契约新字段(见 handoff.validate_contract),
    老契约无此键时为 None(时钟算术会自然跳过,不误报)。
    """
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    items: list[dict] = []
    for ch in chapters:
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        contract = _fresh_contract(row, ch)
        if contract is None:
            continue
        items.append({
            "chapter": ch.chapter_number,
            "in_story_time": contract.get("in_story_time"),
            "story_day": contract.get("story_day"),
            "days_remaining": contract.get("days_remaining"),
            "location": contract.get("location"),
            "scene_continues": bool(contract.get("scene_continues")),
            "time_jump_hint": contract.get("time_jump_hint") or "none",
        })
    return items


def compute_clock(items: list[dict], deadline) -> list[dict]:
    """给每条时间线 item 附加 computed_remaining(按权威天数轴算出的"应剩天数")。

    唯一真相源 = 各章契约 story_day + canon 倒计时定义(deadline):
        elapsed(N)  = story_day(N) − baseline_day
        computed(N) = total_days − elapsed(N)
    baseline_day 取 anchor_chapter 那章的 story_day;该章无 story_day 时降级取
    「章号 ≥ anchor 且最早有 story_day」的章作基准(仍是自 anchor 起算的近似)。

    无倒计时 / total_days<=0 / 定不出基准 / 本章无 story_day → computed 为 None
    (不算、不误报)。返回浅拷贝的新列表(不改入参),每条多一个键
    computed_remaining: int | None。
    """
    out = [dict(i) for i in items]
    total = int(getattr(deadline, "total_days", 0) or 0)
    anchor = int(getattr(deadline, "anchor_chapter", 1) or 1)

    baseline_day: int | None = None
    if deadline is not None and total > 0:
        # 优先 anchor 那章的 story_day 作基准(该章处 elapsed=0,应剩=total)
        for it in out:
            if it.get("chapter") == anchor and it.get("story_day") is not None:
                baseline_day = it["story_day"]
                break
        # 降级:anchor 章缺 story_day → 取 ≥anchor 的最早有值章为基准
        if baseline_day is None:
            for it in sorted(out, key=lambda x: x.get("chapter") or 0):
                if (it.get("chapter") or 0) >= anchor and it.get("story_day") is not None:
                    baseline_day = it["story_day"]
                    break

    for it in out:
        day = it.get("story_day")
        ch = it.get("chapter") or 0
        # 只对 anchor 起算章及其后算倒计时(之前倒计时尚未开始,不设 computed 免得显示 >total)
        if baseline_day is not None and day is not None and ch >= anchor:
            it["computed_remaining"] = total - (day - baseline_day)
        else:
            it["computed_remaining"] = None
    return out


def timeline_block(db: Session, project_id: int, upto: int) -> str:
    """prompt 注入文本块:第 upto 章之前的全书时间线(只带最近 N 条)。

    有倒计时定义时,顶部附一行"权威天数轴表头"(倒计时定义 + 截至上一章的权威值),
    每章行带 story_day 与按权威轴算出的应剩天数——喂给门禁/预审 LLM 的是 Python
    算好的数,LLM 只做"本章正文说的天数对不对得上"比对,不亲自做算术。

    无有效契约 → "(无)"占位(老书时间线断档,由调用方提示先补契约)。
    """
    items = [i for i in book_timeline(db, project_id) if i["chapter"] < upto]
    if not items:
        return "(无全书时间线——老书缺章末契约,可先在编辑部批量补提)"

    project = db.get(Project, project_id)
    deadline = _project_deadline(project)
    items = compute_clock(items, deadline)  # 全量先算(基准 anchor 章可能在截断窗口之外)
    shown = items[-_PROMPT_MAX_ENTRIES:]

    lines = ["(各章章末的剧情时间/地点/故事天数,供判断时间是否倒流、倒计时剩余是否算错)"]

    # 权威天数轴表头:倒计时定义 + 截至上一章的权威值(供 LLM 直接比对)
    if deadline is not None and deadline.total_days > 0 and deadline.name.strip():
        last = items[-1]
        head = (
            f"【倒计时·{deadline.name.strip()}】共 {deadline.total_days} 天,"
            f"自第 {deadline.anchor_chapter} 章起算"
        )
        tail_bits = []
        if last.get("story_day") is not None:
            tail_bits.append(f"已到故事第 {last['story_day']} 天")
        if last.get("computed_remaining") is not None:
            tail_bits.append(f"按权威轴应剩 {last['computed_remaining']} 天")
        if tail_bits:
            head += f";截至第{last['chapter']}章末:" + "、".join(tail_bits)
        lines.append(head + "。本章正文提到的剩余天数须与权威轴一致,不得算乱或前后矛盾。")

    for i in shown:
        seg = f"第{i['chapter']}章末:{i['in_story_time'] or '时间未知'}"
        if i.get("story_day") is not None:
            seg += f"(故事第 {i['story_day']} 天"
            if i.get("computed_remaining") is not None:
                seg += f"·倒计时应剩 {i['computed_remaining']} 天"
            seg += ")"
        if i.get("location"):
            seg += f" @ {i['location']}"
        hint = i.get("time_jump_hint")
        if hint and hint != "none":
            seg += f"(下章跳跃:{hint})"
        lines.append(seg)
    return "\n".join(lines)


def check_story_clock(items: list[dict], deadline, focus_chapter: int) -> list[dict]:
    """确定性故事时钟校验(纯函数,不阻断):只报牵涉最新章 focus_chapter 的矛盾。

    items 需已过 compute_clock(带 computed_remaining)。旧章之间的矛盾已在各自
    生成时查过,这里只锚定 N,天然幂等、O(1)。全部 severity 封顶 major(advisory)。
    四类:①story_day 倒流 ②剩余天数反增 ③口径不符(#3 核心)④期限已过仍续。

    无 story_day / 无倒计时数据的章自然一条不报(降级即静默)。
    """
    ordered = sorted(items, key=lambda x: x.get("chapter") or 0)
    cur = next((i for i in ordered if i.get("chapter") == focus_chapter), None)
    if cur is None:
        return []
    prev = None
    for i in ordered:
        if (i.get("chapter") or 0) < focus_chapter:
            prev = i  # 最后一个章号 < N 的有效契约章(断档也没关系,取最近的)

    day = cur.get("story_day")
    rem = cur.get("days_remaining")
    computed = cur.get("computed_remaining")
    total = int(getattr(deadline, "total_days", 0) or 0)
    anchor = int(getattr(deadline, "anchor_chapter", 1) or 1)
    issues: list[dict] = []

    # ① 故事天数倒流(同日合法,只报严格变小)
    if prev is not None and day is not None and prev.get("story_day") is not None \
            and day < prev["story_day"]:
        issues.append({
            "severity": "major", "type": "timeline",
            "description": (
                f"故事天数倒流:第{prev['chapter']}章末已到故事第 {prev['story_day']} 天,"
                f"第{focus_chapter}章末却回到第 {day} 天(如非刻意闪回,时间不应倒流)。"
            ),
            "suggestion": "核对本章章末落在故事第几天;若确有闪回/回忆,请在正文交代清楚。",
        })

    # ② 倒计时剩余反增(倒计时只会变少)
    if prev is not None and rem is not None and prev.get("days_remaining") is not None \
            and rem > prev["days_remaining"]:
        issues.append({
            "severity": "major", "type": "timeline",
            "description": (
                f"倒计时剩余反增:第{prev['chapter']}章还剩 {prev['days_remaining']} 天,"
                f"第{focus_chapter}章却变成还剩 {rem} 天(倒计时只会变少,不该变多)。"
            ),
            "suggestion": f"核对本章提到的剩余天数,应 ≤ 上一章的 {prev['days_remaining']} 天。",
        })

    # ③ 口径不符(#3 核心):正文明说的剩余 vs 权威轴算出的应剩
    if rem is not None and computed is not None and rem != computed:
        elapsed = total - computed
        issues.append({
            "severity": "major", "type": "timeline",
            "description": (
                f"倒计时口径不符:第{focus_chapter}章正文说还剩 {rem} 天,"
                f"但按权威天数轴应为 {computed} 天(倒计时共 {total} 天,自第 {anchor} 章起算,"
                f"到本章已过 {elapsed} 天)。"
            ),
            "suggestion": f"把本章提到的剩余天数改为 {computed} 天,或核对各章的故事日是否连贯。",
        })

    # ④ 期限已过仍续(权威轴应剩为负,却还有本章正文)
    if computed is not None and computed < 0:
        issues.append({
            "severity": "major", "type": "timeline",
            "description": (
                f"倒计时已超期:按权威天数轴,第{focus_chapter}章末应剩 {computed} 天(负数),"
                f"即已超出总期限 {total} 天,故事却仍在继续而无了结。"
            ),
            "suggestion": "确认期限是否应在此前触发结局/后果,或倒计时定义(总天数/起算章)是否需修正。",
        })

    return issues


def persist_clock_issues(db: Session, project_id: int, chapter: Chapter, text: str) -> None:
    """定稿后跑确定性故事时钟校验,把牵涉本章的矛盾落成 advisory issue(source=clock)。

    读时聚合:book_timeline(含本章刚提交的契约)→ compute_clock → check_story_clock。
    懒导入 checker.persist_issues 避免与 checker 循环导入(checker 已导入本模块)。
    自带 commit;校验为空也照常 persist(幂等清掉本章上一版 clock 建议)。无倒计时
    定义且无 story_day 时 check_story_clock 自然返回空,等价 no-op。

    调用方(apply_chapter_tail)在契约抽取之后调用并 try/except 自吞——纯算术无 LLM,
    但读聚合仍可能撞库,绝不能拖垮章后主链路。
    """
    from app.engines.consistency.checker import persist_issues  # 懒导入破循环

    project = db.get(Project, project_id)
    deadline = _project_deadline(project)
    items = compute_clock(book_timeline(db, project_id), deadline)
    issues = check_story_clock(items, deadline, chapter.chapter_number)
    persist_issues(db, chapter, issues, source="clock", text=text)
    db.commit()


def _project_deadline(project: Project | None):
    """取项目故事宪法里的倒计时定义(CanonDeadline | None);无则 None。"""
    if project is None:
        return None
    from app.schemas.canon import coerce_canon  # 懒导入:schemas 不该被 engines 顶层拉链

    return coerce_canon(project.canon).deadline
