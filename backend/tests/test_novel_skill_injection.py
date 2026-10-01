# tests/test_novel_skill_injection.py
# -*- coding: utf-8 -*-
"""novel 线 skill 包注入(docs/23 P1):

- 零污染:普通书(未挂载)四个节点注入块全空,模板 format 结果不含 Skill 标记;
- 旧语义兼容:全局 enabled 的 novel 包不挂载也生效(与 anime 线同规则);
- 书级挂载:enabled=False 的包靠 Project.mounted_packs 生效,清挂载即退化;
- 预算闸与节点隔离对挂载包同样成立。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Project
from app.db.models.skill_pack import SkillPack
from app.engines.skills.packs import (
    render_project_skill_block,
    render_skill_block,
)
from app.prompts.chapter import CHAPTER_DRAFT_PROMPT

NODES = ("idea", "outline", "draft", "polish")


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _pack(db, key: str, *, enabled: bool, scope=("novel",), node="draft", directive="测试指令:爽点当章兑现。"):
    p = SkillPack(
        pack_key=key, name=key, description="", scope=list(scope),
        entries=[{"node": node, "kind": "directive", "directive": directive}],
        version=1, history=[], enabled=enabled, is_builtin=False,
    )
    db.add(p)
    db.flush()
    return p


def test_zero_pollution_plain_book():
    """普通书:无挂载且无全局启用 novel 包 → 四节点全空串。"""
    db = _db()
    project = Project(title="普通书", mode="serial")
    db.add(project)
    db.flush()
    for node in NODES:
        assert render_project_skill_block(db, project, node) == ""


def test_template_slot_eats_empty_string():
    """skill_block 空串时模板照常 format,产物不含 Skill 标记(逐字零污染)。"""
    db = _db()
    project = Project(title="普通书", mode="serial")
    db.add(project)
    db.flush()
    block = render_project_skill_block(db, project, "draft")
    assert block == ""
    slots = set(__import__("re").findall(r"\{(\w+)\}", CHAPTER_DRAFT_PROMPT))
    text = CHAPTER_DRAFT_PROMPT.format(**{k: "" for k in slots})
    assert "创作 Skill" not in text


def test_global_enabled_pack_still_applies_without_mount():
    """旧语义兼容:全局 enabled 的 novel 包,不挂载也注入(与 anime 线同规则)。"""
    db = _db()
    _pack(db, "global-novel-pack", enabled=True)
    project = Project(title="普通书")
    db.add(project)
    db.flush()
    block = render_project_skill_block(db, project, "draft")
    assert "global-novel-pack" in block


def test_mounted_disabled_pack_applies_only_to_mounting_book():
    """书级挂载:enabled=False 的包,挂载的书注入、没挂的书不注入(爽文双包机制)。"""
    db = _db()
    _pack(db, "drama_source_male", enabled=False)
    drama = Project(title="漫剧源书", mode="drama", audience="male",
                    mounted_packs=["drama_source_male"])
    plain = Project(title="普通书", mode="serial")
    db.add_all([drama, plain])
    db.flush()

    assert "drama_source_male" in render_project_skill_block(db, drama, "draft")
    # 零污染的另一面:同一库里普通书依旧干净
    assert render_project_skill_block(db, plain, "draft") == ""


def test_unmount_degrades_to_plain():
    """退路:清空 mounted_packs 即恢复普通口径(不动全局开关)。"""
    db = _db()
    _pack(db, "drama_source_female", enabled=False)
    drama = Project(title="漫剧源书", mode="drama", audience="female",
                    mounted_packs=["drama_source_female"])
    db.add(drama)
    db.flush()
    assert "drama_source_female" in render_project_skill_block(db, drama, "draft")
    drama.mounted_packs = []
    db.flush()
    assert render_project_skill_block(db, drama, "draft") == ""


def test_node_isolation_for_mounted_pack():
    """节点隔离:挂载包只有 draft 条目,别的节点不注入。"""
    db = _db()
    _pack(db, "drama_source_male", enabled=False, node="draft")
    drama = Project(title="漫剧源书", mode="drama", mounted_packs=["drama_source_male"])
    db.add(drama)
    db.flush()
    assert render_project_skill_block(db, drama, "draft") != ""
    for node in ("idea", "outline", "polish", "shots", "render"):
        assert render_project_skill_block(db, drama, node) == ""


def test_legacy_call_without_mounted_keys_unchanged():
    """旧调用形态(mounted_keys=None)只看 enabled:挂载但全局停用的包不进。"""
    db = _db()
    _pack(db, "drama_source_male", enabled=False)
    assert render_skill_block(db, scope="novel", node="draft") == ""
    # enabled 的照旧
    _pack(db, "global-novel", enabled=True)
    assert "global-novel" in render_skill_block(db, scope="novel", node="draft")


# ---------- P2:真实 seed 的爽文双包(docs/23) ----------

def _mounted_book(db, pack_key: str):
    book = Project(title=f"漫剧源书-{pack_key}", mode="drama",
                   audience="male" if pack_key.endswith("male") else "female",
                   mounted_packs=[pack_key])
    db.add(book)
    db.flush()
    return book


def test_seeded_drama_packs_inject_all_four_nodes():
    """seed 的男频包按书挂载后,idea/outline/draft/polish 四节点条目齐注入。"""
    db = _db()
    book = _mounted_book(db, "drama_source_male")
    idea = render_project_skill_block(db, book, "idea")
    outline = render_project_skill_block(db, book, "outline")
    draft = render_project_skill_block(db, book, "draft")
    polish = render_project_skill_block(db, book, "polish")
    assert "四件套" in idea and "扮猪吃虎" in idea
    assert "双爽点" in outline          # v2 剧本体工艺:章纲双爽点
    assert "对话占七成" in draft and "打脸现场三件套" in draft  # v2:剧本体+示范
    assert "至少两个" in polish          # v2 审校:爽点密度检查
    # 注入预算闸:单节点不超限
    assert all(len(b) <= 1500 for b in (idea, outline, draft, polish))


def test_seeded_packs_channel_distinction():
    """频道可辨:男频 idea 注入含男频皮肤不含女频皮肤,女频反之,不串味。"""
    db = _db()
    male = _mounted_book(db, "drama_source_male")
    female = _mounted_book(db, "drama_source_female")
    m = render_project_skill_block(db, male, "idea")
    f = render_project_skill_block(db, female, "idea")
    assert "玄幻修仙" in m and "宅斗宫斗" not in m
    assert "宅斗宫斗" in f and "玄幻修仙" not in f
    # 公共骨架两包同文(块头的包名除外,包名本就该区分频道)
    def body(b: str) -> str:
        return "\n".join(b.splitlines()[2:])

    assert body(render_project_skill_block(db, male, "draft")) == body(
        render_project_skill_block(db, female, "draft")
    )


def test_seeded_packs_do_not_leak_into_anime_or_plain():
    """seed 后普通书与 anime scope 均不受爽文包影响。"""
    db = _db()
    plain = Project(title="普通书")
    db.add(plain)
    db.flush()
    assert render_project_skill_block(db, plain, "draft") == ""
    assert render_skill_block(db, scope="anime", node="shots") == "" or "drama_source" not in render_skill_block(db, scope="anime", node="shots")


# ---------- P3:建书接口的 drama 行为 ----------

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _auth(client):
    import time
    r = client.post("/api/auth/register", json={
        "username": f"drama_u_{int(time.time() * 1000) % 10 ** 9}",
        "password": "pass123", "invite_code": "test-invite",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_novel_entry_rejects_new_drama_source_books():
    with _client() as client:
        headers = _auth(client)
        for audience in ('male', 'female', ''):
            r = client.post('/api/projects', headers=headers, json={
                'title': '独立漫剧请走新入口', 'mode': 'drama', 'audience': audience,
            })
            assert r.status_code == 400
            assert '原创漫剧' in r.json()['detail']


def test_novel_cannot_be_converted_to_drama_source():
    with _client() as client:
        headers = _auth(client)
        novel = client.post('/api/projects', headers=headers, json={'title': '普通小说'}).json()
        r = client.patch(f"/api/projects/{novel['id']}", headers=headers,
                         json={'mode': 'drama', 'audience': 'male'})
        assert r.status_code == 400
        kept = client.get(f"/api/projects/{novel['id']}", headers=headers).json()
        assert kept['mode'] == 'serial' and kept['mounted_packs'] == []


def test_create_plain_book_unchanged():
    with _client() as client:
        headers = _auth(client)
        r = client.post("/api/projects", headers=headers, json={
            "title": "普通书", "mode": "serial",
        })
        body = r.json()
        assert body["mounted_packs"] == [] and body["audience"] == ""
        assert body["mode"] == "serial"


# ---------- v2:官方包条目升级(未被用户改过的内置包随版本推进) ----------

def test_builtin_pack_upgrade_for_untouched_pack():
    """v1 已 seed 且从未被用户编辑的包:再跑 seed 升到 v2 条目,旧版进 history。"""
    db = _db()
    from app.engines.skills.packs import ensure_builtin_packs
    ensure_builtin_packs(db)  # v2 种入(新库直接是新条目)
    male = next(p for p in db.query(SkillPack).all() if p.pack_key == "drama_source_male")
    # 模拟线上 v1 状态:压回旧条目再 seed,应升级回 v2
    old_entries = [{"node": "draft", "kind": "directive",
                    "directive": "漫剧源书正文口径:对话占比过半(旧版条目)。"}]
    male.entries = old_entries
    male.version = 1
    male.history = []
    db.commit()
    ensure_builtin_packs(db)
    db.refresh(male)
    assert male.version == 2
    joined = "".join(e.get("directive", "") for e in male.entries)
    assert "对话占七成" in joined  # 新条目已就位
    assert any("旧版条目" in str(h.get("entries")) for h in male.history)  # 旧版留痕


def test_builtin_pack_upgrade_skips_user_edited():
    """用户改过的包(history 非空):官方升级不覆盖,版本不动。"""
    db = _db()
    from app.engines.skills.packs import ensure_builtin_packs
    ensure_builtin_packs(db)
    male = next(p for p in db.query(SkillPack).all() if p.pack_key == "drama_source_male")
    # 模拟用户编辑过:history 有记录
    male.history = [{"version": 1, "entries": male.entries}]
    male.version = 2
    kept = str(male.entries)
    db.commit()
    ensure_builtin_packs(db)
    db.refresh(male)
    assert str(male.entries) == kept  # 未被覆盖
