# tests/test_adapt_audit.py
# -*- coding: utf-8 -*-
"""改编质量验收(docs/15 §5.3):保真度核对,确定性零 LLM。

改编此前完全没有验收——小说线 7 项关卡,剧本/漫剧写了就落库,没有东西回答
「改编丢没丢原著的关键设定」。本文件测三件事:
① 保真度(事实→改编稿的字面覆盖,保守判据)
② 取舍声明与稿件的一致性(adapt_note)
③ 产物摊平(三条线各自的格式适配)与 API 端点

测试用隔离内存库(共享库的 create_all 会毒化 alembic_version,导致启动迁移
跳过 FTS5 建表——见 MEMORY.md「测试基建纪律」)。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Chapter, Entity, Fact, Project
from app.engines.adapt_audit import (
    MIN_ADAPT_CHARS,
    _is_deletion_claim,
    _phrases_of,
    audit_adaptation,
    check_fact,
    render_fidelity,
)
from app.engines.adapt_extract import clips_text, drama_episodes_text, script_episodes_text


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(title="改编验收书", target_chapters=5)
    db.add(project)
    db.flush()
    hero = Entity(project_id=project.id, entity_type="character", name="林砚",
                  aliases=[], base_profile={})
    db.add(hero)
    db.flush()
    db.add_all([
        Fact(project_id=project.id, entity_id=hero.id, fact_type="state",
             content="林砚的左臂被剑贯穿,伤口已结痂", valid_from=1,
             importance="critical", source_chapter=1),
        Fact(project_id=project.id, entity_id=hero.id, fact_type="possession",
             content="断锋剑在沈鹤手上", valid_from=2,
             importance="major", source_chapter=2),
        Fact(project_id=project.id, entity_id=hero.id, fact_type="location",
             content="落脚在城南破庙", valid_from=3,
             importance="minor", source_chapter=3),
    ])
    db.commit()
    return db, project


# ==================== 短语切分 ====================

def test_phrases_split_on_both_widths_of_punctuation():
    """半角逗号也必须切:LLM 与手写数据里半角比全角更常见,漏了会让整句
    连成一个「短语」,比对永远命中不了(实测踩过)。"""
    assert _phrases_of("林砚的左臂被剑贯穿,伤口已结痂") == [
        "林砚的左臂被剑贯穿", "伤口已结痂",
    ]
    assert _phrases_of("甲乙丙丁戊,己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]
    # 半角句点同样切分
    assert _phrases_of("甲乙丙丁戊.己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]


def test_phrases_drop_too_short_and_cap():
    # 「重伤」这类两字事实满地字面命中,算了是噪声(阈值 MIN_PHRASE_CHARS=4)
    assert _phrases_of("重伤") == []
    assert _phrases_of("甲乙丙,丁戊己庚") == ["丁戊己庚"]  # 三字短语也丢
    many = ",".join(f"短语编号{i}" for i in range(20))
    assert len(_phrases_of(many)) == 6


# ==================== 单条事实比对 ====================

def test_check_fact_hits_longest_phrase():
    kept, phrase = check_fact(
        "林砚的左臂被剑贯穿,伤口已结痂", "林砚的左臂被剑贯穿。他咬着牙。")
    assert kept and phrase == "林砚的左臂被剑贯穿"
    # 命中短那半句也算保住(任一短语命中即可)
    kept, phrase = check_fact("林砚的左臂被剑贯穿,伤口已结痂", "伤口已结痂,但还疼")
    assert kept and phrase == "伤口已结痂"


def test_check_fact_is_conservative_on_paraphrase():
    """换个说法就算丢失——**这是刻意的**:虚报「保住了」比虚报「丢了」危险
    (前者让人不去查,后者最多多看一眼)。"""
    kept, _ = check_fact("林砚的左臂被剑贯穿,伤口已结痂", "他的胳膊受了剑伤,已经好了")
    assert not kept


def test_check_fact_no_phrase_returns_false():
    assert check_fact("重伤", "他身上有重伤") == (False, "")


# ==================== 保真度总报 ====================

def test_audit_short_text_is_unevaluated_not_zero():
    """还没写出东西 → 报「未评估」(facts_total=0),不是 0% 保真度。"""
    db, project = _db()
    report = audit_adaptation(db, project.id, "太短")
    assert report.adapted_chars < MIN_ADAPT_CHARS
    assert report.facts_total == 0 and report.ratio == 1.0
    assert render_fidelity(report) == ""
    db.close()


def test_audit_full_coverage():
    db, project = _db()
    text = ("林砚的左臂被剑贯穿,伤口已结痂。断锋剑在沈鹤手上。"
            "他落脚在城南破庙,一住就是半个月。" * 3)
    report = audit_adaptation(db, project.id, text)
    assert report.facts_total == 3
    assert report.facts_kept == 3
    assert report.ratio == 1.0
    assert report.lost == [] and report.lost_critical == []
    assert report.gaps == []
    assert "100%" in render_fidelity(report)
    db.close()


def test_audit_reports_lost_critical_separately():
    db, project = _db()
    # 只保住 major 那条(critical 与 minor 丢失)
    text = "断锋剑在沈鹤手上,他一直没松手。" * 8
    report = audit_adaptation(db, project.id, text)
    assert report.facts_total == 3
    assert report.facts_kept == 1
    assert [c.importance for c in report.lost_critical] == ["critical"]
    assert {c.importance for c in report.lost} == {"critical", "minor"}
    rendered = render_fidelity(report)
    assert "33%" in rendered and "未落地(关键)" in rendered
    db.close()


def test_audit_chapter_scoping():
    """给了章号就只核那几章取材的事实(事实是累积语义,第 N 章包含之前所有章)。

    核全书会把「本就不在这部改编里」的事实一起报成丢失,掩盖真问题。
    """
    db, project = _db()
    text = "林砚的左臂被剑贯穿,伤口已结痂。" * 9
    scoped = audit_adaptation(db, project.id, text, chapter_numbers=[1])
    assert scoped.facts_total == 1 and scoped.facts_kept == 1
    # 核到第 3 章 → 三条事实都在范围内,只有第 1 条落地
    from app.engines.adapt import chapter_facts
    assert len(chapter_facts(db, project.id, [3])) == 3
    full = audit_adaptation(db, project.id, text)
    assert full.facts_total == 3 and full.facts_kept == 1
    db.close()


def test_audit_chapter_gaps():
    db, project = _db()
    # 只有第 1 章取材的事实落地 → 第 2/3 章是覆盖断点
    text = "林砚的左臂被剑贯穿,伤口已结痂。" * 9
    report = audit_adaptation(db, project.id, text)
    assert report.gaps == [2, 3]
    assert "整章未落地" in render_fidelity(report)
    db.close()


# ==================== 取舍声明一致性 ====================

def test_note_issues_detects_unrealized_claim():
    db, project = _db()
    text = "断锋剑在沈鹤手上,他一直没松手,反手把剑横在身前。" * 6
    report = audit_adaptation(
        db, project.id, text,
        adapt_note="保留了林砚的左臂被剑贯穿这条设定。",
    )
    assert any("找不到" in i for i in report.note_issues), report.note_issues
    db.close()


def test_note_issues_detects_stale_deletion_claim():
    """声明说删了、稿里还在 → 改完忘了改声明,或删得不干净。"""
    db, project = _db()
    text = "林砚的左臂被剑贯穿,伤口已结痂。断锋剑在沈鹤手上。" * 6
    report = audit_adaptation(
        db, project.id, text,
        adapt_note="删掉林砚的左臂被剑贯穿这条,改用环境暗示。",
    )
    assert any("仍有" in i for i in report.note_issues), report.note_issues
    db.close()


def test_note_issues_clean_when_consistent():
    db, project = _db()
    text = "林砚的左臂被剑贯穿,伤口已结痂。断锋剑在沈鹤手上。" * 6
    report = audit_adaptation(db, project.id, text, adapt_note="保留主线冲突。")
    assert report.note_issues == []
    db.close()


def test_is_deletion_claim_scoped_to_same_sentence():
    assert _is_deletion_claim("删掉X。保留Y。", "X")
    assert not _is_deletion_claim("删掉X。保留Y。", "Y")
    assert not _is_deletion_claim("保留了X。", "X")


# ==================== 产物摊平(三条线各一套格式) ====================

class _Ep:
    def __init__(self, content):
        self.content = content


def test_script_episodes_text_skips_meta_and_blank():
    eps = [
        _Ep("Title: 第 1 集\nAuthor: 某某\n\n内景 破庙 - 夜\n林砚握剑而立。\n\n"),
        _Ep(""),  # 未写集:整段为空
    ]
    text = script_episodes_text(eps)
    assert "林砚握剑而立。" in text
    assert "Title:" not in text and "Author:" not in text


class _DramaEp:
    def __init__(self, script):
        self.script = script


def test_drama_episodes_text_joins_text_and_action_not_speaker():
    eps = [_DramaEp({"lines": [
        {"speaker": "沈鹤", "text": "剑在我手上", "action": "把剑横在身前"},
        {"speaker": "旁白", "text": "", "action": "血滴在石板上"},
    ]})]
    text = drama_episodes_text(eps)
    assert "剑在我手上" in text and "把剑横在身前" in text
    assert "血滴在石板上" in text
    assert "沈鹤" not in text  # 人名单独出现不构成设定落地


def test_drama_episodes_text_survives_bad_shapes():
    assert drama_episodes_text([_DramaEp(None), _DramaEp("不是 dict"),
                                _DramaEp({"lines": "不是列表"}),
                                _DramaEp({"lines": [None, "坏条目"]})]) == ""


class _Shot:
    def __init__(self, **kw):
        self.action_desc = kw.get("action_desc", "")
        self.dialogue = kw.get("dialogue", "")
        self.prompt_cn = kw.get("prompt_cn", "")


def test_clips_text_collects_three_fields():
    text = clips_text([_Shot(action_desc="左臂缠着布", dialogue="我不疼",
                             prompt_cn="特写,血痕")])
    assert "左臂缠着布" in text and "我不疼" in text and "血痕" in text


# ==================== 反向验证:核心判据真的参与了判定 ====================

def test_reverse_verification_matching_is_load_bearing():
    """把「命中」判据换成恒真,保真度必须变(否则说明断言没绑定实现)。"""
    db, project = _db()
    text = "完全无关的一段文字,反复写很多遍凑长度。" * 6
    report = audit_adaptation(db, project.id, text)
    assert report.facts_kept == 0  # 与上面 full_coverage 形成对照

    import app.engines.adapt_audit as mod
    orig = mod.check_fact
    mod.check_fact = lambda content, adapted: (True, "SENTINEL")
    try:
        forced = mod.audit_adaptation(db, project.id, text)
        assert forced.facts_kept == forced.facts_total  # 哨兵确实被吃进去了
    finally:
        mod.check_fact = orig
    # 还原后回到真判据
    assert mod.audit_adaptation(db, project.id, text).facts_kept == 0
    db.close()
