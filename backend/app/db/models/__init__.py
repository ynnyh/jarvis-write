"""数据模型汇总。

在此统一导入所有 ORM 模型,确保:
1. create_all / migrate.py 能发现全部表(项目不用 Alembic);
2. 外部按 `from app.db.models import Project, Outline, ...` 使用。

表设计详见 docs/02-data-model.md。
"""
from app.db.models.project import Project, Architecture
from app.db.models.outline import Outline, OutlineVersion
from app.db.models.chapter import Chapter
from app.db.models.story_bible import (
    Entity,
    Fact,
    Relationship,
    KnowledgeState,
)
from app.db.models.foreshadowing import Foreshadowing
from app.db.models.preset import TendencyPreset
from app.db.models.setting import ProviderSetting
from app.db.models.summary import ChapterSummary
from app.db.models.usage import LlmUsage
from app.db.models.user import User

__all__ = [
    "User",
    "Project",
    "Architecture",
    "Outline",
    "OutlineVersion",
    "Chapter",
    "Entity",
    "Fact",
    "Relationship",
    "KnowledgeState",
    "Foreshadowing",
    "TendencyPreset",
    "ProviderSetting",
    "ChapterSummary",
    "LlmUsage",
]
