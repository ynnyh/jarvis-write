# app/engines/pipeline/retrieval.py
# -*- coding: utf-8 -*-
"""检索式注入:给一个场景,只捞它需要的事实,而不是把全库灌进去。

为什么需要这一层(结构性诊断之二:「注入全、无检索」):
  CHAPTER_DRAFT_PROMPT 有 31 个占位符,全量灌满。这不是「给得多所以更准」——
  模型的注意力预算是有限的,把整本圣经摊在面前,它只能用最保守的方式处理:
  挑几条最安全的遵守,其余当背景噪音。结果就是「设定都对、但写得很平」。
  真正需要的是:写这一场时,只把这一场会用到的那几条事实摆到它面前。

复用已有地基(关键发现):
  alembic 0008 已经建好 5 张 FTS5 虚拟表(fts_chapters/fts_outlines/
  fts_entities/fts_facts/fts_foreshadowings),tokenize='trigram'(中文可用),
  触发器齐备、索引实时同步。**但此前只开给了用户搜正文,从没喂给模型。**
  所以检索层的 MVP 不需要向量库——FTS5 + 结构化过滤就够了。

检索策略(三路并行,再合并排序):
  ① 字面路:场景卡的 title/summary/goal/conflict/fact_hints 拼成查询串,
     走 FTS5 bm25 检索 facts / entities / foreshadowings / 前章正文。
  ② 结构化路:本章出场人物的当前有效事实(时序过滤 valid_from/valid_until),
     按重要度排序——这一路不走字面匹配,因为「主角此刻身体什么状态」这类
     信息与场景文本没有词汇重叠,纯字面检索必然漏。
  ③ 时效路:伏笔到期提醒、常驻装置催场——这几项本来就有确定性引擎,
     这里只是把它们的结果合并进同一个「本场须知」块。

排序:critical 永远在最前,其次 major,再次是字面命中得分高的。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.db.models import Fact, Scene

logger = logging.getLogger("jarvis-write.retrieval")

# 单场景注入的事实条数上限。一章 3-5 场,每场带 8-12 条最相关的事实,
# 全章累积仍是「按需取」而非「全量灌」——这正是这一层的意义。
MAX_FACTS_PER_SCENE = 12
# 字面路最多取多少条候选(再和结构化路合并去重)
MAX_LITERAL_HITS = 24

# 重要度排序权重:critical 永不被截断
_IMPORTANCE_RANK = {"critical": 0, "major": 1, "minor": 2}

# 中文停用词/无检索价值的词:场景文本里的虚词不该参与 FTS 查询
_STOPWORDS = frozenset(
    "的了在是和与及或也都很就要会有没有这不一个我们你们他们它们"
    "自己什么怎么为什么因为所以但是然后而且以及如果虽然因为"
)

# FTS5 MATCH 查询串里必须转义的字符(trigram 分词器下双引号是短语语法)
_FTS_UNSAFE = re.compile(r'["\'()*:^\-]')


@dataclass
class SceneContext:
    """一个场景的「须知」:检索结果的结构化载体。

    分成四槽是因为它们的注入方式不同:
      facts       → 渲染成硬约束行(带重要度标记)
      entities    → 渲染成名册(防凭空冒人)
      foreshadows → 渲染成伏笔提醒(带逾期/临近标记)
      motifs      → 已写滥的桥段(反面清单)
    """

    facts: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    foreshadows: list[dict] = field(default_factory=list)
    motifs: list[dict] = field(default_factory=list)
    # 检索诊断(评测与前端展示用:命中了多少条是字面路捞的)
    stats: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.facts or self.entities or self.foreshadows or self.motifs)


def _fts_available(db: Session) -> bool:
    """FTS5 虚表是否可用(全新库未跑迁移 0008 时为 False,回落到结构化路)。"""
    try:
        row = db.execute(
            sql_text(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='fts_facts' LIMIT 1"
            )
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001 — 非 SQLite 或权限问题一律当不可用
        return False


def build_query(scene: Scene) -> str:
    """场景卡 → FTS 查询串。

    只用内容性字段(title/summary/goal/conflict/fact_hints),不用张力档这类
    结构性字段——「5」这个数字拿去检索没有意义。

    **只保留 ≥3 字的词**(实测踩过的坑):
    0008 用的 trigram 分词器把文本切成 3 字符的片段,查询词短于 3 字符时
    根本不在倒排表里。更要命的是 FTS5 对这种情况**不报错、直接返回 0 行**——
    一个 2 字词混进查询串,整个 MATCH 就静默失效。曾在真实场景上实测:
    `祭坛 玄铁令 认出仇人` 命中 0 行,而 `玄铁令` 命中 1 行。
    所以 2 字词必须在这里剔除,而不是留给下游处理。
    """
    parts: list[str] = []
    for raw in (
        scene.title,
        scene.summary,
        scene.goal,
        scene.conflict,
        " ".join(str(h) for h in (scene.fact_hints or [])),
    ):
        if raw:
            parts.append(str(raw))
    text = " ".join(parts)
    # 中文按 3 字以上连续片段切(trigram 的最小可检索单位),英文按 ≥3 字母切
    tokens = re.findall(r"[\u4e00-\u9fff]{3,}|[A-Za-z]{3,}", text)
    kept: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        kept.append(tok)
        if len(kept) >= 20:  # 查询串过长会淹没真正相关的词
            break
    return " ".join(kept)


def _fts_or_query(tokens: list[str]) -> str:
    """把词表拼成 FTS5 的 OR 查询。

    为什么必须用 OR 而不是空格(空格是隐式 AND):
    场景卡的文本跨度很大(标题讲地点、目标是「确认仇人身份」),一个事实几乎
    不可能同时含所有这些词。用 AND 检索结果是 0 行;OR 才能召回「含其中任一
    相关词」的事实,再由 bm25 把命中多的排前面。
    """
    safe = [t for t in tokens if len(t) >= 3 and not _FTS_UNSAFE.search(t)]
    if not safe:
        return ""
    return " OR ".join(safe)


def _literal_facts(db: Session, project_id: int, query: str, chapter_number: int) -> list[dict]:
    """字面路:bm25 检索事实,按时序与项目过滤。

    时序过滤必须在 SQL 里做(而不是捞回来再过滤):一本 100 章的书有几万条
    事实,先全部捞出再筛会让这一层比全量注入还慢。
    """
    match_q = _fts_or_query(query.split())
    if not match_q:
        return []
    try:
        rows = db.execute(
            sql_text(
                "SELECT f.rowid, f.content, f.entity_id, bm25(fts_facts) AS score "
                "FROM fts_facts f "
                "WHERE fts_facts MATCH :q AND f.project_id = :pid "
                "ORDER BY score LIMIT :lim"
            ),
            {"q": match_q, "pid": project_id, "lim": MAX_LITERAL_HITS},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 查询语法/虚表异常不应阻断生成
        logger.debug("字面路检索失败(回落结构化路):%s", exc)
        return []

    if not rows:
        return []

    # bm25 命中只给了 rowid,重要度/fact_type/时序要回源表取(FTS 虚表里没有这些列)
    ids = [r[0] for r in rows]
    facts = {
        f.id: f
        for f in db.query(Fact).filter(Fact.id.in_(ids)).all()
    }
    score_of = {r[0]: r[3] for r in rows}

    out: list[dict] = []
    for fact_id in ids:
        f = facts.get(fact_id)
        if f is None:
            continue
        # 时序:只留这一章时刻仍有效的事实(FTS 虚表里没有 valid_* 列)
        if f.valid_from is not None and f.valid_from > chapter_number:
            continue
        if f.valid_until is not None and f.valid_until < chapter_number:
            continue
        out.append(
            {
                "id": f.id,
                "content": f.content,
                "entity_id": f.entity_id,
                "importance": f.importance or "major",
                "fact_type": f.fact_type or "",
                "valid_from": f.valid_from,
                "_src": "literal",
                "_score": score_of.get(fact_id, 0.0),
            }
        )
    return out


def _literal_entity_ids(db: Session, project_id: int, query: str) -> set[int]:
    """字面路:检索实体名。返回 entity id 集合,供结构化路加权。"""
    match_q = _fts_or_query(query.split())
    if not match_q:
        return set()
    try:
        rows = db.execute(
            sql_text(
                "SELECT e.rowid FROM fts_entities e "
                "WHERE fts_entities MATCH :q AND e.project_id = :pid "
                "ORDER BY bm25(fts_entities) LIMIT :lim"
            ),
            {"q": match_q, "pid": project_id, "lim": MAX_LITERAL_HITS},
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def _literal_chapter_tails(
    db: Session, project_id: int, query: str, chapter_number: int, limit: int = 2
) -> list[str]:
    """字面路:在前章正文里找与这一场最相关的段落。

    这是「无检索」最痛的缺口:此前只注入最近 2 章的尾部,如果本场要回收的是
    第 3 章埋的一个细节,那两章尾部里根本没有,模型只能凭摘要的二手描述瞎猜。
    命中片段直接给原文,模型看到的是一手材料。
    """
    match_q = _fts_or_query(query.split())
    if not match_q:
        return []
    try:
        rows = db.execute(
            sql_text(
                "SELECT rowid FROM fts_chapters "
                "WHERE fts_chapters MATCH :q AND project_id = :pid "
                "  AND chapter_number < :ch "
                "ORDER BY bm25(fts_chapters) LIMIT :lim"
            ),
            {"q": match_q, "pid": project_id, "ch": chapter_number, "lim": limit},
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []

    from app.db.models import Chapter

    out: list[str] = []
    for (rowid,) in rows:
        ch = db.query(Chapter).filter(Chapter.id == rowid).first()
        if ch is None:
            continue
        body = ch.final_content or ch.draft_content or ""
        if not body:
            continue
        window = _best_window(body, query.split())
        if window:
            out.append(window)
    return out


def _best_window(body: str, tokens: list[str], window: int = 400) -> str:
    """在正文里定位与查询最相关的一段,返回窗口原文。

    不用 snippet():trigram 分词器下 snippet 的分词边界会把中文切得破碎。
    这里自己做:按固定步长滑窗,统计每个窗口命中的查询词数,取命中最密处。

    tokens 传的是**原始词表**(不是 OR 拼好的查询串)——这里做的是子串计数,
    拼串里的 "OR" 会被当成词去数,必然出错。
    """
    toks = [t for t in tokens if t]
    if not toks:
        return body[:window]
    best_pos, best_hits = 0, 0
    step = max(1, window // 2)
    for start in range(0, max(1, len(body)), step):
        seg = body[start : start + window]
        hits = sum(seg.count(t) for t in toks)
        if hits > best_hits:
            best_hits, best_pos = hits, start
    if best_hits <= 0:
        return ""
    return body[best_pos : best_pos + window]


def _structured_facts(
    db: Session, project_id: int, chapter_number: int, entity_names: list[str]
) -> list[dict]:
    """结构化路:本章出场人物的当前有效事实。

    不走字面匹配:「主角此刻带伤」这类信息与场景文本没有词汇重叠,
    纯字面检索必然漏。按重要度排序,超限时先砍 minor。
    """
    from app.engines.consistency import BibleService

    bible = BibleService(db, project_id)
    facts = bible.query_facts_at(chapter_number, entity_names or None)
    retired = bible.retired_entity_ids()
    if retired:
        facts = [f for f in facts if f.entity_id not in retired]

    rank = _IMPORTANCE_RANK
    facts.sort(key=lambda f: rank.get(f.importance, 1))
    out: list[dict] = []
    for f in facts:
        out.append(
            {
                "id": f.id,
                "content": f.content,
                "entity_id": f.entity_id,
                "entity_name": bible.entity_name(f.entity_id),
                "importance": f.importance or "major",
                "fact_type": f.fact_type or "",
                "valid_from": f.valid_from,
                "_src": "structured",
                "_score": 0.0,
            }
        )
    return out


def _merge_and_rank(
    literal: list[dict],
    structured: list[dict],
    boosted_entity_ids: set[int],
    limit: int = MAX_FACTS_PER_SCENE,
) -> list[dict]:
    """合并两路结果,去重,按「重要度 → 是否字面命中 → 命中实体是否相关」排序。

    并入的字面命中打上 _src="both" 标记(两路都捞到 = 强相关),
    这在评测里可以用来判断检索层是否真的起了作用。
    """
    by_id: dict[int, dict] = {}
    literal_ids = {f["id"] for f in literal}
    for f in structured:
        by_id[f["id"]] = dict(f)
    for f in literal:
        if f["id"] in by_id:
            by_id[f["id"]]["_src"] = "both"
            by_id[f["id"]]["_score"] = f["_score"]
        else:
            by_id[f["id"]] = dict(f)

    def sort_key(f: dict):
        imp = _IMPORTANCE_RANK.get(f.get("importance"), 1)
        # 字面命中优先(说明与这一场的内容真的相关);两路都命中最高
        src_rank = {"both": 0, "literal": 1, "structured": 2}.get(f.get("_src"), 3)
        # 命中实体与场景人物重合的加权(与这一场的人相关的事实更该出现)
        ent_bonus = 0 if f.get("entity_id") in boosted_entity_ids else 1
        return (imp, src_rank if imp > 0 else 0, ent_bonus, f.get("_score") or 0.0)

    # critical 单独保底:排序后仍保证不被 minor 挤掉
    merged = sorted(by_id.values(), key=sort_key)
    kept = merged[:limit]
    critical = [f for f in merged if f.get("importance") == "critical"]
    for f in critical:
        if f not in kept:
            # 用 critical 替换掉末尾的 minor
            kept = kept[: max(0, limit - 1)] + [f]
    return kept[:limit]


def _entity_slot(db: Session, project_id: int, entity_ids: set[int], limit: int = 20) -> list[dict]:
    """名册槽:场景涉及的人物 + 字面命中的实体。"""
    from app.db.models import Entity

    if not entity_ids:
        return []
    rows = (
        db.query(Entity)
        .filter(Entity.project_id == project_id, Entity.id.in_(entity_ids))
        .limit(limit)
        .all()
    )
    return [
        {"id": e.id, "name": e.name, "entity_type": e.entity_type}
        for e in rows
        if not e.retired
    ]


def retrieve_for_scene(
    db: Session,
    project_id: int,
    scene: Scene,
    chapter_number: int,
) -> SceneContext:
    """给一个场景捞它需要的事实(这一层的唯一入口)。

    任何一个子步骤失败都不抛出:检索是增强,不是依赖。取不到就退化为
    「结构化路 + 空字面路」,再取不到就返回空上下文——生成仍然能跑。
    """
    query = build_query(scene)
    entity_names = [str(c) for c in (scene.characters or [])]

    has_fts = _fts_available(db)
    literal: list[dict] = []
    boosted: set[int] = set()
    tail_excerpts: list[str] = []
    if has_fts and query:
        literal = _literal_facts(db, project_id, query, chapter_number)
        boosted = _literal_entity_ids(db, project_id, query)
        tail_excerpts = _literal_chapter_tails(db, project_id, query, chapter_number)

    try:
        structured = _structured_facts(db, project_id, chapter_number, entity_names)
    except Exception as exc:  # noqa: BLE001 — 结构化路失败则只有字面路
        logger.debug("结构化路检索失败:%s", exc)
        structured = []

    facts = _merge_and_rank(literal, structured, boosted)

    # 名册:场景人物解析出的实体 + 字面命中实体
    ent_ids = set(boosted)
    try:
        from app.engines.consistency import BibleService

        bible = BibleService(db, project_id)
        for name in entity_names:
            ent = bible.find_entity(name)
            if ent is not None:
                ent_ids.add(ent.id)
    except Exception:  # noqa: BLE001
        pass
    entities = _entity_slot(db, project_id, ent_ids)

    ctx = SceneContext(
        facts=facts,
        entities=entities,
        stats={
            "query_terms": len(query.split()),
            "literal_hits": len(literal),
            "structured_hits": len(structured),
            "merged_facts": len(facts),
            "tail_excerpts": len(tail_excerpts),
            "fts": has_fts,
        },
    )
    if tail_excerpts:
        ctx.stats["tail_texts"] = tail_excerpts
    return ctx


def render_facts_block(ctx: SceneContext) -> str:
    """事实槽 → prompt 文本(带重要度标记与来源章号)。"""
    if not ctx.facts:
        return "(本场暂无需要特别核对的状态约束)"
    lines: list[str] = []
    for f in ctx.facts:
        mark = "❗" if f.get("importance") == "critical" else "·"
        who = f.get("entity_name") or ""
        prefix = f"{who}:" if who else ""
        if f.get("valid_from"):
            lines.append(f"{mark} {prefix}{f['content']}(自第{f['valid_from']}章起)")
        else:
            lines.append(f"{mark} {prefix}{f['content']}")
    return "\n".join(lines)


def render_tail_block(ctx: SceneContext) -> str:
    """前文相关原文片段 → prompt 文本(一手材料,替代二手摘要)。"""
    tails = ctx.stats.get("tail_texts") or []
    if not tails:
        return ""
    blocks = "\n\n".join(f"【前文相关片段 {i}】\n{t}" for i, t in enumerate(tails, 1))
    return "\n\n" + blocks + "\n"
