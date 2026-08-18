# tests/test_medium_backend.py
# -*- coding: utf-8 -*-
"""中档健壮性回归:审校分数容错 / 伏笔模糊匹配歧义不猜 / 硬约束块 Top-N 截断。

都不碰 LLM:_clamp_score 是纯函数,另两项用真·文件库直接建行验证。
"""
from __future__ import annotations


def test_clamp_score_tolerates_garbage():
    """LLM 把分数写成非数字/越界/None 时,_clamp_score 一律安全落地,绝不抛 ValueError。

    老版 review_chapter 直接 int(scores.get(k)),遇到 "优秀"/"8分" 会 ValueError
    穿透、带崩整个主审。
    """
    from app.engines.editorial import _clamp_score

    # 合法值:整数/字符串/浮点都吃,钳到 1-10
    assert _clamp_score(8) == 8
    assert _clamp_score("8") == 8
    assert _clamp_score(8.5) == 8
    assert _clamp_score("8.5") == 8
    assert _clamp_score(99) == 10       # 越界上钳
    assert _clamp_score(-3) == 1        # 越界下钳到 1(与既有主审契约一致)
    assert _clamp_score(0) == 0         # 明确 0 分/缺维度 → "—"
    # 非法值:一律 0,不抛异常
    for bad in (None, "", "优秀", "8分", "N/A", [], {}, "nan"):
        assert _clamp_score(bad) == 0, f"{bad!r} 应安全落到 0"


def test_find_by_description_no_ambiguous_guess():
    """伏笔描述定位:精确/去空白命中,唯一子串才认账,歧义(多条子串命中)不猜。

    老版「包含即匹配 + 取第一条」会把 reinforce/payoff 误挂到错误伏笔上。
    """
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Foreshadowing, Project
    from app.db.session import SessionLocal, engine
    from app.engines.consistency.foreshadow import ForeshadowScheduler

    Base.metadata.create_all(engine)

    db = SessionLocal()
    proj = Project(title="fs-test")
    db.add(proj)
    db.flush()
    pid = proj.id

    def _fs(desc: str) -> None:
        db.add(Foreshadowing(
            project_id=pid, description=desc, chapter_planted=1, status="planted",
        ))

    _fs("主角腰间的青铜钥匙")
    _fs("他袖中藏 着一封 信笺")          # 内部有空格,测去空白匹配
    _fs("他左肩有一道旧伤疤和刺青")       # 与下一条共享前缀,制造子串歧义
    _fs("他左肩有一道旧伤疤很显眼")
    db.commit()

    sch = ForeshadowScheduler(db, pid)
    find = sch._find_by_description

    # 1) 精确
    assert find("主角腰间的青铜钥匙").description == "主角腰间的青铜钥匙"
    # 2) 去空白后精确(查询串没有空格,库里那条有)
    assert find("他袖中藏着一封信笺").description == "他袖中藏 着一封 信笺"
    # 3) 唯一子串命中(库里描述是查询串的子串,且仅此一条)
    hit = find("主角腰间的青铜钥匙其实能打开东侧密室的暗门")
    assert hit is not None and hit.description == "主角腰间的青铜钥匙"
    # 4) 歧义:"他左肩有一道旧伤疤" 是两条描述的公共子串 → 不猜,返回 None
    assert find("他左肩有一道旧伤疤") is None
    # 5) 过短子串(<8 字)不做模糊,避免乱配
    assert find("青铜") is None

    db.close()


def test_hard_constraints_block_caps_but_keeps_critical():
    """硬约束块超上限时按重要度截断:critical 全保,只砍 minor/major,总行数不超上限。"""
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Entity, Fact, Project
    from app.db.session import SessionLocal, engine
    from app.engines.consistency.bible import BibleService, _MAX_FACT_LINES

    Base.metadata.create_all(engine)

    db = SessionLocal()
    proj = Project(title="bible-cap")
    db.add(proj)
    db.flush()
    pid = proj.id
    ent = Entity(project_id=pid, entity_type="character", name="主角")
    db.add(ent)
    db.flush()

    n_critical = 5
    n_minor = _MAX_FACT_LINES + 20  # 远超上限,确保发生截断
    for i in range(n_critical):
        db.add(Fact(
            project_id=pid, entity_id=ent.id, fact_type="state",
            content=f"CRIT{i}", importance="critical",
            valid_from=1, valid_until=None, source_chapter=1,
        ))
    for i in range(n_minor):
        db.add(Fact(
            project_id=pid, entity_id=ent.id, fact_type="state",
            content=f"minor{i}", importance="minor",
            valid_from=1, valid_until=None, source_chapter=1,
        ))
    db.commit()

    block = BibleService(db, pid).hard_constraints_block(10)
    lines = [ln for ln in block.split("\n") if ln.strip()]
    db.close()

    assert len(lines) == _MAX_FACT_LINES, f"应截断到上限 {_MAX_FACT_LINES},实际 {len(lines)}"
    for i in range(n_critical):
        assert f"CRIT{i}" in block, f"critical 事实 CRIT{i} 被截掉了,不允许"
