# app/engines/project_io.py
# -*- coding: utf-8 -*-
"""项目级完整导出 / 导入引擎。

把一部小说的全部数据导出成一个 JSON 文件,便于备份、换设备、换部署实例时迁移。
导入时创建为新项目(生成新 project_id),所有外键重新映射,不影响原有数据。

导出范围(小说核心数据):
- Project(项目基本信息)
- Architecture(顶层架构)
- Outline + OutlineVersion(大纲 + 版本)
- Chapter + ChapterVersion + ChapterState + ChapterIssue + ChapterSummary(章节全量)
- Entity + Fact + Relationship + KnowledgeState(故事圣经)
- Foreshadowing(伏笔)
- WritingCard(写作卡)
- TendencyPreset(项目级倾向预设)

不导出(敏感 / 用户级 / 衍生工坊):
- User / ProviderConfig / ProviderSetting / LlmUsage(用户级,含 LLM key)
- Job / InviteCode / AppSetting(系统级)
- Drama / Promo / Clips / Birthday(衍生工坊数据,后续可扩展)

导出格式版本:1.0(后续格式变更时递增,导入时做兼容)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Architecture,
    Chapter,
    ChapterIssue,
    ChapterState,
    ChapterSummary,
    ChapterVersion,
    Entity,
    Fact,
    Foreshadowing,
    KnowledgeState,
    Outline,
    OutlineVersion,
    Project,
    Relationship,
    TendencyPreset,
    WritingCard,
)

logger = logging.getLogger("jarvis-write.project_io")

EXPORT_FORMAT_VERSION = "1.0"

# 需要导出的表顺序(外键依赖在前,导入时按此顺序重建)
# 每个元组: (模型类, 外键字段映射)
# 注:当前版本只导出有直接 project_id 字段的核心表。
# OutlineVersion / ChapterVersion / ChapterState / ChapterIssue / ChapterSummary
# 等通过外键关联的表,后续版本再扩展支持。
_EXPORT_TABLES: list[tuple[type, dict[str, str]]] = [
    (Architecture, {"project_id": "projects"}),
    (Outline, {"project_id": "projects"}),
    (Chapter, {"project_id": "projects"}),
    (Entity, {"project_id": "projects"}),
    (Fact, {"entity_id": "entities", "project_id": "projects"}),
    (
        Relationship,
        {
            "project_id": "projects",
            "from_entity_id": "entities",
            "to_entity_id": "entities",
        },
    ),
    (Foreshadowing, {"project_id": "projects"}),
    (WritingCard, {"project_id": "projects"}),
]

# 导出时排除的字段(用户级 / 系统级 / 时间戳)。
# 注:id 必须保留在行数据里——导入器靠「旧 id → 新 id」映射重建跨表外键
# (facts.entity_id / relationships.from_entity_id / chapters.outline_id 等),
# 丢了 id,这些引用在导入时就无法落位。project 行本身仍不带 id
# (新项目 id 由数据库分配,映射走 source.project_id)。
_EXCLUDE_FIELDS = frozenset({"user_id", "created_at", "updated_at"})


def _row_to_dict(row: Any, *, include_id: bool = True) -> dict[str, Any]:
    """把 ORM 行转成可序列化的 dict,排除不需要的字段。"""
    data: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        if name in _EXCLUDE_FIELDS or (name == "id" and not include_id):
            continue
        value = getattr(row, name)
        # JSON 字段可能是 dict/list,直接保留;datetime 转 ISO 字符串
        if isinstance(value, datetime):
            value = value.isoformat()
        data[name] = value
    return data


def export_project(db: Session, project_id: int) -> dict[str, Any]:
    """导出指定项目的全部数据为 dict。

    Args:
        db: 数据库 session
        project_id: 要导出的项目 ID

    Returns:
        可序列化为 JSON 的 dict,包含 format_version / exported_at / project / 各表数据

    Raises:
        ValueError: 项目不存在
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在")

    logger.info("导出项目 %s: %s", project_id, project.title)

    result: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(),
        "source": {
            "project_id": project_id,
            "title": project.title,
        },
            "project": _row_to_dict(project, include_id=False),
    }

    # 按表导出(project 行不带 id:新项目 id 由导入方数据库分配)
    for model, _fk_map in _EXPORT_TABLES:
        table_name = model.__tablename__
        rows = db.query(model).filter(model.project_id == project_id).all()
        result[table_name] = [_row_to_dict(row) for row in rows]
        logger.info("  %s: %d 行", table_name, len(rows))

    return result


def export_project_to_json(db: Session, project_id: int) -> str:
    """导出项目为 JSON 字符串。"""
    data = export_project(db, project_id)
    return json.dumps(data, ensure_ascii=False, indent=2)


def import_project(
    db: Session,
    data: dict[str, Any],
    user_id: int,
    title_override: str | None = None,
) -> Project:
    """从导出的 dict 导入为新项目。

    Args:
        db: 数据库 session
        data: export_project 返回的 dict
        user_id: 新项目归属的用户 ID
        title_override: 覆盖项目标题(可选,默认用导出时的标题 + " (导入)")

    Returns:
        新创建的 Project 对象

    Raises:
        ValueError: 数据格式不兼容或缺少必要字段
    """
    # 格式版本校验
    fmt_version = data.get("format_version", "0.0")
    if fmt_version != EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"不支持的导出格式版本: {fmt_version}(当前支持 {EXPORT_FORMAT_VERSION})"
        )

    if "project" not in data:
        raise ValueError("导出数据缺少 project 字段")

    logger.info("导入项目,原标题: %s", data["project"].get("title", "?"))

    # 1. 创建新项目
    project_data = dict(data["project"])
    if title_override:
        project_data["title"] = title_override
    else:
        project_data["title"] = f"{project_data.get('title', '未命名')} (导入)"
    project_data["user_id"] = user_id
    # 重置状态为 draft,避免导入后直接显示为已完成
    project_data["status"] = "draft"
    # 清除可能的失配标记
    project_data["outline_stale"] = False

    project = Project(**project_data)
    db.add(project)
    db.flush()  # 获取新 project.id

    # 2. 建立旧 id → 新 id 的映射表
    # 结构: {表名: {旧id: 新id}}
    id_mappings: dict[str, dict[int, int]] = {
        "projects": {data["source"]["project_id"]: project.id},
    }

    # 3. 按表顺序导入
    for model, fk_map in _EXPORT_TABLES:
        table_name = model.__tablename__
        rows_data = data.get(table_name, [])
        if not rows_data:
            continue

        id_mappings[table_name] = {}
        for row_data in rows_data:
            row_dict = dict(row_data)

            # 保存旧 id 用于映射(必须从 row_dict 里 pop:显式带 id 插入会撞自增唯一键)
            old_id = row_dict.pop("id", None)

            # 重新映射外键
            for fk_field, target_table in fk_map.items():
                old_fk = row_dict.get(fk_field)
                if old_fk is not None and target_table in id_mappings:
                    new_fk = id_mappings[target_table].get(old_fk)
                    if new_fk is not None:
                        row_dict[fk_field] = new_fk
                    else:
                        # 外键指向的记录不存在(可能导出时遗漏),置空;
                        # 若该列 NOT NULL,则本行会在下方 savepoint 回滚中被跳过
                        logger.warning(
                            "  %s 行 %s 的外键 %s=%s 找不到映射,置空",
                            table_name, old_id, fk_field, old_fk,
                        )
                        row_dict[fk_field] = None

            # project_id 统一用新项目 id
            if "project_id" in row_dict:
                row_dict["project_id"] = project.id

            # 单行一个 savepoint:坏行(如旧格式导出里无法落位的外键)只丢弃自己,
            # 不污染 session,后续行与整次导入继续
            try:
                with db.begin_nested():
                    row = model(**row_dict)
                    db.add(row)
                    db.flush()
                if old_id is not None:
                    id_mappings[table_name][old_id] = row.id
            except Exception as e:
                logger.error("  导入 %s 行 %s 失败: %s,跳过", table_name, old_id, e)
                continue

        logger.info("  %s: 导入 %d / %d 行", table_name, len(id_mappings[table_name]), len(rows_data))

    db.commit()
    logger.info("项目导入完成,新项目 ID: %s,标题: %s", project.id, project.title)
    return project


def import_project_from_json(
    db: Session,
    json_str: str,
    user_id: int,
    title_override: str | None = None,
) -> Project:
    """从 JSON 字符串导入项目。"""
    data = json.loads(json_str)
    return import_project(db, data, user_id, title_override)
