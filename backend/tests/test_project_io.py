# tests/test_project_io.py
# -*- coding: utf-8 -*-
"""项目级完整导出 / 导入测试。

验证:
1. 导出功能:能把项目数据导出为 JSON,包含所有核心表
2. 导入功能:能从 JSON 导入为新项目,外键正确映射,数据完整
3. 往返一致性:导出 → 导入 → 再导出,核心数据一致
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.db.models import (
    Chapter,
    Entity,
    Fact,
    Foreshadowing,
    Outline,
    Project,
    Relationship,
    User,
)
from app.db.session import SessionLocal
from app.engines.project_io import export_project, import_project
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(client):
    """每个测试用独立的 session,用完即关。
    依赖 client fixture 以触发 lifespan 建表。
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db: Session) -> User:
    """创建测试用户。用户名用 uuid:测试库整个 pytest 会话共用,users 行只增不删,
    用 id(db)(内存地址)当名字会在地址被回收复用时撞上早前测试的同名行。"""
    user = User(
        username=f"test_io_{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("pass123"),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def sample_project(db: Session, test_user: User) -> Project:
    """创建一个带基础数据的测试项目。"""
    project = Project(
        user_id=test_user.id,
        title="测试小说",
        topic="一个测试故事",
        genre="玄幻",
        target_chapters=10,
    )
    db.add(project)
    db.flush()

    # 大纲
    outline = Outline(
        project_id=project.id,
        chapter_number=1,
        title="第一章",
        summary="开篇",
    )
    db.add(outline)

    # 章节
    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        draft_content="这是第一章的草稿内容。",
        final_content="这是第一章的定稿内容。",
        status="drafted",
    )
    db.add(chapter)

    # 实体
    entity = Entity(
        project_id=project.id,
        name="主角",
        entity_type="character",
    )
    db.add(entity)
    db.flush()

    # 时序事实(挂在实体上,导出/导入需重建 entity_id 映射)
    db.add(
        Fact(
            project_id=project.id,
            entity_id=entity.id,
            fact_type="state",
            content="练气六层,修为五年未进",
            valid_from=1,
            valid_until=None,
            importance="major",
            source_chapter=1,
        )
    )

    # 关系边(双向实体外键,同需重建映射)
    entity_b = Entity(
        project_id=project.id,
        name="师父",
        entity_type="character",
    )
    db.add(entity_b)
    db.flush()
    db.add(
        Relationship(
            project_id=project.id,
            from_entity_id=entity.id,
            to_entity_id=entity_b.id,
            relation="师徒",
            valid_from=1,
            valid_until=None,
        )
    )

    db.commit()
    db.refresh(project)
    return project


def test_export_project_contains_all_tables(db: Session, sample_project: Project):
    """导出应包含所有核心表的数据。"""
    data = export_project(db, sample_project.id)

    assert data["format_version"] == "1.0"
    assert data["project"]["title"] == "测试小说"
    assert data["project"]["topic"] == "一个测试故事"

    # 各表数据存在
    assert len(data["outlines"]) == 1
    assert len(data["chapters"]) == 1
    assert len(data["entities"]) == 2

    # 不包含敏感字段
    assert "user_id" not in data["project"]
    assert "id" not in data["project"]


def test_export_nonexistent_project_raises(db: Session):
    """导出不存在的项目应抛出 ValueError。"""
    with pytest.raises(ValueError, match="不存在"):
        export_project(db, 99999)


def test_import_project_creates_new_project(db: Session, sample_project: Project, test_user: User):
    """导入应创建为新项目,不影响原项目。"""
    data = export_project(db, sample_project.id)
    original_count = db.query(Project).count()

    new_project = import_project(db, data, user_id=test_user.id)

    # 新项目创建成功
    assert new_project.id != sample_project.id
    assert new_project.title == "测试小说 (导入)"
    assert new_project.user_id == test_user.id

    # 项目数量增加
    assert db.query(Project).count() == original_count + 1

    # 原项目不受影响
    original = db.get(Project, sample_project.id)
    assert original.title == "测试小说"


def test_import_project_with_title_override(db: Session, sample_project: Project, test_user: User):
    """导入时可覆盖项目标题。"""
    data = export_project(db, sample_project.id)
    new_project = import_project(db, data, user_id=test_user.id, title_override="我的新项目")

    assert new_project.title == "我的新项目"


def test_import_project_data_integrity(db: Session, sample_project: Project, test_user: User):
    """导入后各表数据应完整,外键正确映射。"""
    data = export_project(db, sample_project.id)
    new_project = import_project(db, data, user_id=test_user.id)

    # 大纲
    outlines = db.query(Outline).filter(Outline.project_id == new_project.id).all()
    assert len(outlines) == 1
    assert outlines[0].title == "第一章"

    # 章节
    chapters = db.query(Chapter).filter(Chapter.project_id == new_project.id).all()
    assert len(chapters) == 1
    assert chapters[0].chapter_number == 1
    assert "第一章的定稿内容" in chapters[0].final_content

    # 实体
    entities = db.query(Entity).filter(Entity.project_id == new_project.id).all()
    assert {e.name for e in entities} == {"主角", "师父"}


def test_export_import_roundtrip(db: Session, sample_project: Project, test_user: User):
    """导出 → 导入 → 再导出,核心数据应一致。"""
    # 第一次导出
    data1 = export_project(db, sample_project.id)

    # 导入
    new_project = import_project(db, data1, user_id=test_user.id)

    # 第二次导出
    data2 = export_project(db, new_project.id)

    # 核心数据一致(排除 id 等自增字段)
    assert len(data1["outlines"]) == len(data2["outlines"])
    assert len(data1["chapters"]) == len(data2["chapters"])
    assert len(data1["entities"]) == len(data2["entities"])

    # 章节内容一致
    assert data1["chapters"][0]["final_content"] == data2["chapters"][0]["final_content"]


def test_roundtrip_rebinds_fact_and_relationship_fks(
    db: Session, sample_project: Project, test_user: User
):
    """事实/关系行的实体外键在导入后必须落到新实体 id 上(回归:旧版导出丢 id,
    facts.entity_id 无法映射,导入直接崩)。"""
    data = export_project(db, sample_project.id)
    new_project = import_project(db, data, user_id=test_user.id)

    new_entities = {e.name: e.id for e in db.query(Entity).filter(Entity.project_id == new_project.id)}
    assert set(new_entities) == {"主角", "师父"}

    facts = db.query(Fact).filter(Fact.project_id == new_project.id).all()
    assert len(facts) == 1
    assert facts[0].entity_id == new_entities["主角"]
    assert facts[0].content == "练气六层,修为五年未进"

    rels = db.query(Relationship).filter(Relationship.project_id == new_project.id).all()
    assert len(rels) == 1
    assert rels[0].from_entity_id == new_entities["主角"]
    assert rels[0].to_entity_id == new_entities["师父"]


def test_import_skips_poisoned_row_without_aborting(
    db: Session, sample_project: Project, test_user: User
):
    """单行外键无法落位(脏数据/旧格式导出)只跳过该行,不拖垮整次导入。"""
    data = export_project(db, sample_project.id)
    good_fact_count = len(data["facts"])
    data["facts"].append(
        {
            "id": 99999,
            "project_id": data["source"]["project_id"],
            "entity_id": 424242,  # 不存在的实体
            "fact_type": "state",
            "content": "孤儿事实",
            "valid_from": 1,
            "valid_until": None,
            "importance": "major",
            "source_chapter": 1,
        }
    )

    new_project = import_project(db, data, user_id=test_user.id)

    # 章节/实体等其他表不受影响
    assert db.query(Chapter).filter(Chapter.project_id == new_project.id).count() == 1
    # 坏行被跳过,好行照常进入
    facts = db.query(Fact).filter(Fact.project_id == new_project.id).all()
    assert len(facts) == good_fact_count
    assert all(f.content != "孤儿事实" for f in facts)


def test_import_invalid_format_version(db: Session, sample_project: Project, test_user: User):
    """导入不支持的格式版本应抛出 ValueError。"""
    data = export_project(db, sample_project.id)
    data["format_version"] = "99.0"

    with pytest.raises(ValueError, match="不支持的导出格式版本"):
        import_project(db, data, user_id=test_user.id)


def test_import_missing_project_field(db: Session, test_user: User):
    """导入缺少 project 字段的数据应抛出 ValueError。"""
    data = {"format_version": "1.0", "exported_at": "2026-01-01T00:00:00"}

    with pytest.raises(ValueError, match="缺少 project 字段"):
        import_project(db, data, user_id=test_user.id)
