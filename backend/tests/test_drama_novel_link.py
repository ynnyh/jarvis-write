# tests/test_drama_novel_link.py
# -*- coding: utf-8 -*-
"""漫剧打通第一期「接血」单测(内存库 + mock LLM,无需 API key)。

验证点(小说侧资产真正进漫剧 prompt):
- dna_block / profile_block / book_block:本书基因与创作偏好档案的渲染与空态
- 切集:EPISODE_PLAN prompt 里带上 本书基因 + 作者雷区(带「源正文不在此列」边界),
  episodes 照常归一化落库
- chapters_final_text:超预算的章保头尾去中段(章尾=卡点素材,不许砍),短章全文保留
- 角色卡 digest:结构化三段含现行关系边;按事实条数排序、超出 _MAX_CHARACTERS
  不再静默截断(characters_total/characters_shown 透出)
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def _make_db(*, dna=None, tendency=None):
    """独立内存库:一个项目(可带 dna/global_tendency)+ 蓝图/正文/标记/实体工厂。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(
        title="打通测试书", genre="古风权谋", target_chapters=10,
        target_words_per_chapter=3000,
        dna=dna, global_tendency=tendency or {},
    )
    db.add(project)
    db.commit()

    def add_outline(n: int, summary: str, beats=None):
        from app.db.models import Outline

        db.add(Outline(
            project_id=project.id, chapter_number=n, title=f"第{n}章题",
            chapter_purpose="推进", summary=summary,
            beats=beats or [], current_version=1,
        ))
        db.commit()

    return db, project, add_outline


DNA = {
    "comps": "像《琅琊榜》遇上《甄嬛传》", "mode": "mixed",
    "axes": {"pace": "快", "sweetness": "虐"},
    "must": ["权谋过招"], "must_not": ["开挂金手指"],
    "capsule": "冷峻克制,棋局感",
}
TENDENCY = {"_profile": {"style": "冷峻权谋风", "taboos": "不写降智反派", "audience": " adult"}}


class _Adapter:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


# ---------- 书级资产收口 ----------

def test_dna_and_profile_blocks_render_and_empty():
    from app.engines.drama.common import book_block, dna_block, profile_block

    db, p, _ = _make_db(dna=DNA, tendency=TENDENCY)
    dna = dna_block(p)
    assert "本书基因" in dna and "琅琊榜" in dna and "开挂金手指" in dna and "冷峻克制" in dna
    prof = profile_block(p)
    assert "创作偏好档案" in prof and "冷峻权谋风" in prof and "不写降智反派" in prof
    assert "本书基因" in book_block(p) and "创作偏好档案" in book_block(p)

    # 空 dna / 空档案 → 空串(零 token,零行为变化)
    db2, p2, _ = _make_db()
    assert dna_block(p2) == "" and profile_block(p2) == "" and book_block(p2) == ""
    db.close()
    db2.close()


def test_plan_prompt_carries_book_blocks_and_banned():
    """切集 prompt 必须带上 本书基因 + 创作偏好 + 作者雷区(带源正文边界说明)。"""
    from app.db.models import Outline
    from app.engines.consistency import motifs
    from app.engines.drama.planner import plan_episodes

    db, p, add_outline = _make_db(dna=DNA, tendency=TENDENCY)
    for n in (1, 2):
        add_outline(n, f"第{n}章剧情概要", beats=["起", "承"])
    motifs.add_banned(db, p.id, "铁锈玫瑰", "写烦了的意象")
    motifs.add_banned(db, p.id, "躺下等天亮")
    db.commit()

    adapter = _Adapter(json.dumps({"episodes": [
        {"title": "棋局开场", "source_chapters": [1],
         "hook": "御书房惊变", "recap": "夺嫡开局", "cliffhanger": "密诏失踪"},
    ]}, ensure_ascii=False))
    with patch("app.engines.drama.planner.get_adapter_for", return_value=adapter):
        eps = __import__("asyncio").run(
            plan_episodes(db, p, 1, 2, "dialogue", 90)
        )
    assert len(eps) == 1
    prompt = adapter.prompts[0]
    assert "本书基因" in prompt and "琅琊榜" in prompt
    assert "创作偏好档案" in prompt and "不写降智反派" in prompt
    assert "作者雷区" in prompt and "铁锈玫瑰" in prompt and "躺下等天亮" in prompt
    assert "源正文里已有的内容不在此列" in prompt  # 边界:忠实改编不受雷区约束
    assert "第1章剧情概要" in prompt.replace(" ", "")  # 蓝图素材仍在

    # 无雷区/无基因的书:块整体消失,prompt 干净
    db2, p2, add2 = _make_db()
    add2(1, "普通剧情")
    adapter2 = _Adapter(json.dumps({"episodes": [
        {"title": "平铺直叙", "source_chapters": [1], "hook": "h", "recap": "r", "cliffhanger": "c"},
    ]}, ensure_ascii=False))
    with patch("app.engines.drama.planner.get_adapter_for", return_value=adapter2):
        __import__("asyncio").run(plan_episodes(db2, p2, 1, 1, "dialogue", 90))
    assert "作者雷区" not in adapter2.prompts[0]
    assert "本书基因" not in adapter2.prompts[0]
    db.close()
    db2.close()


# ---------- 剧本正文选文:头尾保留 ----------

def test_chapters_final_text_keeps_head_and_tail():
    from app.engines.drama.common import chapters_final_text

    db, p, _ = _make_db()
    from app.db.models import Chapter

    long_text = "开头衔接段。" + "中间填充" * 900 + "结尾卡点,密诏失踪。"
    db.add(Chapter(project_id=p.id, outline_id=1, chapter_number=1,
                   final_content=long_text, word_count=len(long_text), status="approved"))
    short = "短章全文,一句到底。"
    db.add(Chapter(project_id=p.id, outline_id=2, chapter_number=2,
                   final_content=short, word_count=len(short), status="approved"))
    db.commit()

    body, got = chapters_final_text(db, p.id, [1, 2], budget=2000)
    assert got == [1, 2]
    assert "开头衔接段" in body  # 头保留(衔接上文)
    assert "结尾卡点,密诏失踪" in body  # 尾保留(卡点素材)——旧的从头截断会砍掉它
    assert "中略" in body  # 中段省略有标记
    assert "短章全文" in body  # 未超预算的章全文保留
    assert len(body) <= 2000
    db.close()


# ---------- 角色卡 digest 与截断明示 ----------

def _entity(db, project_id, name, profile=None):
    from app.db.models import Entity

    e = Entity(project_id=project_id, name=name, entity_type="character",
               base_profile=profile or {})
    db.add(e)
    db.commit()
    return e


def test_entity_digest_includes_relations_and_clip():
    from app.db.models import Fact, Relationship
    from app.engines.drama.characters import _entity_digest

    db, p, _ = _make_db()
    a = _entity(db, p.id, "沈之砚", {"身份": "权臣", "外貌": "月白长袍,左脸刀疤"})
    b = _entity(db, p.id, "姜元淳", {"身份": "帝王"})
    db.add(Fact(project_id=p.id, entity_id=a.id, fact_type="state", content="右手旧伤未愈",
                importance="major", valid_from=1))
    db.add(Relationship(project_id=p.id, from_entity_id=a.id, to_entity_id=b.id,
                        relation="君臣相疑", valid_from=2))
    db.commit()

    digest = _entity_digest(db, a)
    assert "权臣" in digest and "月白长袍" in digest
    assert "右手旧伤未愈" in digest
    assert "与姜元淳:君臣相疑" in digest  # 关系边进 digest——旧版完全没有
    db.close()


def test_character_cards_sorted_by_facts_and_truncation_visible():
    """14 个角色(戏份悬殊)→ 只出 12 张:按事实条数排序,总数明示不静默。"""
    from app.db.models import Fact
    from app.engines.drama.characters import generate_character_cards

    db, p, _ = _make_db()
    star = _entity(db, p.id, "主角甲", {"身份": "主角"})
    guest = _entity(db, p.id, "龙套丙")
    for i in range(13):
        for e in (star, guest):
            db.add(Fact(project_id=p.id, entity_id=e.id, fact_type="state",
                        content=f"{e.name}事实{i}", importance="minor", valid_from=i + 1))
    others = []
    for i in range(12):
        e = _entity(db, p.id, f"配角{i}号")
        db.add(Fact(project_id=p.id, entity_id=e.id, fact_type="state", content=f"事实{i}",
                    importance="minor", valid_from=1))
        others.append(e)
    db.commit()  # 14 个角色:主角甲/龙套丙各 13 条事实,12 个配角各 1 条

    reply = json.dumps({"cards": [
        {"name": "主角甲", "gender": "male", "appearance_cn": "x", "appearance_en": "x",
         "outfit_cn": "x", "voice_desc": "x"},
    ]}, ensure_ascii=False)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=_Adapter(reply)):
        result = __import__("asyncio").run(generate_character_cards(db, p))

    assert result["characters_total"] == 14
    assert result["characters_shown"] == 12
    db.close()


def test_generate_without_entities_still_errors():
    from app.engines.drama.characters import DramaAssetError, generate_character_cards

    db, p, _ = _make_db()
    with pytest.raises(DramaAssetError):
        __import__("asyncio").run(generate_character_cards(db, p))
    db.close()
