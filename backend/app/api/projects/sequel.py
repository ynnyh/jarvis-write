# app/api/projects/sequel.py
# -*- coding: utf-8 -*-
"""开续集(作者诉求 2026-09-16):基于一部旧书(系统写的或导入的)开第二部/第三部。

三件作者点名要做的事:
1. **方向可选**:AI 依据前作出 8 个续集方向卡(sequel-directions-async),
   作者挑一张或整批重摇(带 avoid 防重复),也可手填覆盖;
2. **前作分析**(sequel-analyze 任务):前情提要(人物/事件链/未收伏笔/结尾停点)
   + 文风技法画像(视角/句式节奏/对话密度/常用手法)——提要落新书第 0 章摘要行
   (`_rolling_summary` 查 chapter_number < current 会注入所有新章,全书自带上一部
   记忆),画像写进新书 style_memo(注入每次生成的文风块,正向锚定通道);
3. **每章字数对齐**:续集目标章内字数 = 前作实际章节字数中位数(不吃项目设置,
   那值可能已和成文漂移),蓝图与正文生成、字数守卫全按它来。

资产继承:文风倾向/基因/备忘、架构四块、核心梗卡草稿、人物档案与关系现状
(边 valid_from=0 表示「续集开场现状」;前作具体事件事实不复制——它们绑前作
章节号,复制进新书会污染一致性引擎的时间轴)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import (
    Architecture,
    Chapter,
    ChapterSummary,
    Entity,
    Premise,
    Project,
    Relationship,
    User,
)
from app.db.session import get_db
from app.jobs import spawn_job

from ._common import _get_project_or_404

router = APIRouter()


# 方向卡数量(作者点名:八个方向可选)
_DIRECTION_COUNT = 8
# style_memo 少于这个字数视为「没有成体系的文风画像」,需要分析补写
_STYLE_MEMO_MIN_CHARS = 100


class SequelRequest(BaseModel):
    title: str = Field(default="", max_length=200, description="续集书名;空则《前作》续集")
    direction: str = Field(default="", max_length=800, description="续集方向(选卡或手填)")


class SequelDirection(BaseModel):
    title: str = Field(description="方向名(4-10 字)")
    desc: str = Field(description="这个方向讲什么:主线冲突 + 承接哪些线(60-120 字)")


class DirectionsRequest(BaseModel):
    avoid: list[str] = Field(default_factory=list, description="已出过的方向名,重摇时避开")


class DirectionsOut(BaseModel):
    directions: list[SequelDirection]


class SequelOut(BaseModel):
    project_id: int
    analyze_job_id: str | None = None  # 前作分析进行中时非空,完成自动写入提要与文风画像


@router.post("/{project_id}/sequel-directions-async")
async def sequel_directions_async(
    project_id: int,
    req: DirectionsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 出续集方向卡(8 张):依据前作的人物/未收线/结尾停点,整批可重摇。"""
    src = _get_project_or_404(db, project_id)
    avoid = [str(a).strip() for a in req.avoid if str(a).strip()][:20]

    async def work(progress):
        progress("采样前作内容")
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            samples, chapter_sizes = _sample_source(session, src.id)
            digest = _last_rolling_summary(session, src.id)
            if samples is None:
                return {"directions": []}
            progress("AI 出续集方向")
            from app.engines.consistency.extractor import parse_llm_json
            from app.llm.router import Task, get_adapter_for

            median_words = sorted(chapter_sizes)[len(chapter_sizes) // 2] if chapter_sizes else 0
            prompt = "\n".join([
                "你是一部长篇小说的编辑部主编。第一部已完,作者要开第二部。",
                f"请依据第一部的内容,出{_DIRECTION_COUNT}个**彼此明显不同**的续集方向,"
                "覆盖不同套路(如:主线直进/新势力登场/旧敌归来/视角人物主述/时间跳跃/"
                "支线爆点/世界观扩张/代价清算等,按本书实际情况设计,不硬凑)。",
                "每个方向给:title(4-10 字方向名)和 desc(60-120 字:第二部主线讲什么、"
                "核心冲突是什么、承接第一部哪些未收的线、开篇从哪起势)。",
                "方向必须长在第一部的人物与伏笔上,是「这一部自然长出来的下一步」,不是套话。",
                f"续集每章目标字数约 {median_words} 字(与第一部一致),方向的体量设计要与此匹配。",
                "只输出 JSON,不要解释:",
                '{"directions": [{"title": "…", "desc": "…"}, …]}',
            ])
            if digest:
                prompt += "\n\n【第一部前情提要】\n" + digest[:2000]
            if avoid:
                prompt += ("\n\n【已出过的方向(这次一个都不要重复,也不要换皮重出)】\n"
                           + "\n".join(f"- {a}" for a in avoid))
            prompt += "\n\n【第一部正文采样(全书均匀分层节选)】\n" + samples
            raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
            data = parse_llm_json(raw)
            cards = [
                SequelDirection(
                    title=str(c.get("title") or "").strip()[:20],
                    desc=str(c.get("desc") or "").strip(),
                )
                for c in (data.get("directions") or [])
                if isinstance(c, dict) and str(c.get("title") or "").strip()
            ]
            return {"directions": [c.model_dump() for c in cards[:_DIRECTION_COUNT]]}
        finally:
            session.close()

    return {"job_id": spawn_job(f"sequel-dir-{src.id}", work)}


@router.post("/{project_id}/sequel", response_model=SequelOut)
async def create_sequel(
    project_id: int,
    req: SequelRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按选定方向开续集:复制可继承资产 + 前作分析任务(提要+文风画像)。"""
    src = _get_project_or_404(db, project_id)

    digest_text = _last_rolling_summary(db, src.id)

    # ---- 每章字数与前作对齐:按前作实际章节字数中位数定目标 ----
    src_chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == src.id, Chapter.final_content != "")
        .all()
    )
    if src_chapters:
        sizes = sorted(len(c.final_content) for c in src_chapters)
        target_words = sizes[len(sizes) // 2] or src.target_words_per_chapter
    else:
        target_words = src.target_words_per_chapter

    title = req.title.strip() or f"{src.title}·续集"
    project = Project(
        user_id=src.user_id,
        title=title[:200],
        topic=(req.direction.strip() or f"《{src.title}》的续集,承接上一部的人物与走向继续展开"),
        genre=src.genre,
        target_chapters=src.target_chapters,
        target_words_per_chapter=target_words,
        global_tendency=src.global_tendency,
        style_memo=src.style_memo,
        dna=src.dna,
        sequel_of_id=src.id,
    )
    db.add(project)
    db.flush()

    # ---- 架构(续集沿用世界观/人物动力学是常态,作者可改可重生成) ----
    src_arch = (
        db.query(Architecture).filter(Architecture.project_id == src.id).first()
    )
    if src_arch is not None:
        db.add(Architecture(
            project_id=project.id,
            core_seed=src_arch.core_seed,
            character_dynamics=src_arch.character_dynamics,
            world_building=src_arch.world_building,
            plot_architecture=src_arch.plot_architecture,
            version=1,
        ))

    # ---- 核心梗卡(复制为草稿基线:续集可沿用循环梗,也可改写) ----
    src_premise = (
        db.query(Premise).filter(Premise.project_id == src.id, Premise.kind == "main").first()
    )
    if src_premise is not None:
        db.add(Premise(
            project_id=project.id, kind="main",
            high_concept=src_premise.high_concept,
            payoff=src_premise.payoff,
            beats=list(src_premise.beats or []),
            boundaries=list(src_premise.boundaries or []),
            hook_plan=dict(src_premise.hook_plan or {}),
            source=src_premise.source,
        ))

    # ---- 人物档案 + 关系现状 ----
    src_entities = db.query(Entity).filter(Entity.project_id == src.id).all()
    id_map: dict[int, int] = {}
    for e in src_entities:
        ne = Entity(
            project_id=project.id, entity_type=e.entity_type, name=e.name,
            aliases=list(e.aliases or []), base_profile=dict(e.base_profile or {}),
            retired=e.retired,
        )
        db.add(ne)
        db.flush()
        id_map[e.id] = ne.id
    src_edges = db.query(Relationship).filter(Relationship.project_id == src.id).all()
    for r in src_edges:
        if r.from_entity_id not in id_map or r.to_entity_id not in id_map:
            continue
        db.add(Relationship(
            project_id=project.id,
            from_entity_id=id_map[r.from_entity_id],
            to_entity_id=id_map[r.to_entity_id],
            relation=r.relation,
            valid_from=0,
            valid_until=None,
            evidence_fact_id=None,
            status="confirmed",
        ))

    # ---- 前作分析(前情提要 + 文风技法画像) ----
    # 提要已有(前作末章滚动摘要)就直接落第 0 章摘要行;文风画像缺(导入书/短 memo)
    # 或提要缺,起 analyze 任务:提要→第 0 章摘要行,画像→style_memo(注入每次生成)。
    analyze_job_id: str | None = None
    if digest_text:
        db.add(ChapterSummary(project_id=project.id, chapter_number=0, rolling_summary=digest_text))
    need_style = len((src.style_memo or "").strip()) < _STYLE_MEMO_MIN_CHARS
    if not digest_text or need_style:
        analyze_job_id = _spawn_analyze_job(
            project.id, src.id, need_digest=not digest_text, need_style=need_style,
        )

    db.commit()
    return SequelOut(project_id=project.id, analyze_job_id=analyze_job_id)


def _last_rolling_summary(db: Session, project_id: int) -> str:
    row = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project_id,
            ChapterSummary.rolling_summary != "",
        )
        .order_by(ChapterSummary.chapter_number.desc())
        .first()
    )
    return (row.rolling_summary if row else "").strip()


# 前作采样规模:全书均匀分层取 6 章,每章截断——**常数开销,与书的体量无关**
# (300 万字和 3000 万字的分析成本一样)。分层比只看头尾更能代表全书的文风:
# 千章长卷的笔法会漂移,首/1/3/中/2/3/尾各采一段才立得住。
_SLICE_COUNT = 6
_SLICE_CHARS = 2500


def _sample_source(db: Session, project_id: int) -> tuple[str | None, list[int]]:
    """前作正文采样(全书均匀分层)与各章字数表;没有正文返回 (None, [])。

    总输入量恒定在 _SLICE_COUNT × _SLICE_CHARS ≈ 1.5 万字,token 花销可预算;
    章数不足以铺满分层时退化为「有几分层采几分」。
    """
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    if not chapters:
        return None, []
    n = len(chapters)
    if n <= _SLICE_COUNT:
        picked = chapters
    else:
        # 均匀分层:首章必采,末章必采,中间等距铺开(浮点定位再取整,避免都挤在开头)
        idxs = sorted({round(i * (n - 1) / (_SLICE_COUNT - 1)) for i in range(_SLICE_COUNT)})
        picked = [chapters[i] for i in idxs]
    samples = "\n\n".join(
        f"【第{c.chapter_number}章(节选)】{c.final_content[:_SLICE_CHARS]}"
        for c in picked
    )
    sizes = [len(c.final_content) for c in chapters]
    return samples, sizes


def _spawn_analyze_job(new_pid: int, src_pid: int, *, need_digest: bool, need_style: bool) -> str:
    """前作分析任务:前情提要 → 第 0 章摘要行;文风技法画像 → style_memo。"""
    from app.db.session import SessionLocal

    async def work(progress) -> None:
        progress("采样前作内容")
        session = SessionLocal()
        try:
            samples, sizes = _sample_source(session, src_pid)
            if samples is None:
                return  # 前作没有正文,新书不带前情,作者自己填
            median_words = sorted(sizes)[len(sizes) // 2] if sizes else 0
            progress("AI 分析前情与文风")
            from app.engines.consistency.extractor import parse_llm_json
            from app.llm.router import Task, get_adapter_for

            asks: list[str] = []
            if need_digest:
                asks.append(
                    '"digest": "前情提要,800-1200 字:主要人物及关系与结局状态、主线事件链、'
                    "未收束的伏笔与悬念、结尾停在何处、章回节奏与每章大致篇幅;只陈述事实\""
                )
            if need_style:
                asks.append(
                    '"style_profile": "文风技法画像,300-500 字,可执行可模仿:叙事视角与人称、'
                    "句式长短与节奏、对话与描写的比例、高频修辞与惯用手法、氛围与基调、"
                    "章节开头的起势方式与结尾的钩法;写成给续集写手的工作指令,不是夸奖\""
                )
            prompt = "\n".join([
                "你是一部长篇小说的编辑部主编。作者要开写续集,需要你从第一部里提炼两样东西。",
                "只输出 JSON,不要解释:",
                "{" + ", ".join(asks) + "}",
                "",
                f"【续集每章目标字数】约 {median_words} 字(与第一部一致,提要里的篇幅观察按此口径)",
                "",
                "【第一部正文采样(全书均匀分层节选)】",
                samples,
            ])
            raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
            data = parse_llm_json(raw)
            if not isinstance(data, dict):
                data = {}
            if need_digest:
                digest = str(data.get("digest") or "").strip()
                if not digest:
                    # 模型没按 JSON 出:整篇按提要兜底,总比没有强
                    digest = raw.strip()[:3000]
                row = (
                    session.query(ChapterSummary)
                    .filter(ChapterSummary.project_id == new_pid, ChapterSummary.chapter_number == 0)
                    .first()
                )
                if row is None:
                    row = ChapterSummary(project_id=new_pid, chapter_number=0)
                    session.add(row)
                row.rolling_summary = digest
            if need_style:
                profile = str(data.get("style_profile") or "").strip()
                if profile:
                    project = session.get(Project, new_pid)
                    if project is not None:
                        memo = (project.style_memo or "").strip()
                        project.style_memo = (
                            f"{memo}\n" if memo else ""
                        ) + f"【承前作文风画像(续集必须延续这部书的笔法)】\n{profile}"
            session.commit()
        finally:
            session.close()

    return spawn_job(f"sequel-analyze-{new_pid}", work)
