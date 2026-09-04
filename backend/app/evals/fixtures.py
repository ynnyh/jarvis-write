# app/evals/fixtures.py
# -*- coding: utf-8 -*-
"""黄金样本书夹具:一本小说「开书」阶段的全部输入(架构 + 逐章蓝图),JSON 一份。

评测的可比性全靠它:两次运行必须写同一本书、同样的蓝图,数字差异才归得到 prompt /
模型 / 判据的变化上。夹具只含开书输入、不含正文——正文正是被评测的对象。

两种来源:
- 内置夹具(fixtures/*.json):手写的小书,刻意埋了硬事实(失聪的耳朵、破漏的丹田、
  编号的冷柜……)让一致性门禁有东西可咬;
- 从已有书导出(export_project):把你真写过的书的架构 + 前 N 章蓝图抽成夹具,评测就在
  你自己的题材上跑。

灌库走的是线上同一条路(save_architecture / save_blueprint),版本快照、
content_hash 一应俱全,generate_chapter 看到的项目与用户手动开书的毫无二致。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Outline, Project
from app.engines.pipeline.architecture import ArchitectureResult, save_architecture
from app.engines.pipeline.blueprint import save_blueprint

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# 评测灌进库里的项目统一带这个前缀:runner 据此拒绝往「有真书」的库里跑
TITLE_PREFIX = "[评测] "
ARCH_FIELDS = ("core_seed", "character_dynamics", "world_building", "plot_architecture")
OUTLINE_FIELDS = (
    "title",
    "chapter_role",
    "chapter_purpose",
    "suspense_level",
    "foreshadowing",
    "plot_twist_level",
    "summary",
    "beats",
    "characters_involved",
    "key_items",
    "scene_location",
)


@dataclass
class Fixture:
    name: str
    title: str
    topic: str
    genre: str
    architecture: dict[str, str]
    outlines: list[dict[str, Any]]
    target_words: int = 2500
    global_tendency: dict[str, Any] = field(default_factory=dict)
    world_rules: str = ""
    concept: dict[str, Any] | None = None
    dna: dict[str, Any] | None = None
    review_threshold: int = 7
    # 给人看的:这本夹具埋了哪些硬事实、评测时该盯什么
    notes: str = ""

    @property
    def chapter_count(self) -> int:
        return len(self.outlines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_fixtures() -> list[str]:
    """内置夹具名(不含 .json)。"""
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def _fixture_path(name_or_path: str | Path) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    bundled = FIXTURES_DIR / f"{p.name}.json"
    if bundled.exists():
        return bundled
    raise FileNotFoundError(
        f"夹具不存在: {name_or_path}(内置夹具: {', '.join(list_fixtures()) or '无'})"
    )


def validate_fixture(fx: Fixture) -> list[str]:
    """夹具体检,返回问题列表(空 = 合格)。"""
    errors: list[str] = []
    if not (fx.title or "").strip():
        errors.append("title 不能为空")
    if fx.target_words <= 0:
        errors.append("target_words 必须 > 0")
    if not isinstance(fx.architecture, dict):
        errors.append("architecture 必须是对象")
    else:
        for key in ARCH_FIELDS:
            if not str(fx.architecture.get(key) or "").strip():
                errors.append(f"architecture.{key} 不能为空")
    if not fx.outlines:
        errors.append("outlines 至少一章")
    numbers = [o.get("chapter_number") for o in fx.outlines]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"outlines.chapter_number 必须从 1 连续编号,实际 {numbers}")
    for o in fx.outlines:
        n = o.get("chapter_number")
        if not str(o.get("title") or "").strip():
            errors.append(f"第 {n} 章 title 不能为空")
        if not str(o.get("summary") or "").strip():
            errors.append(f"第 {n} 章 summary 不能为空")
        beats = o.get("beats", [])
        if not isinstance(beats, list) or not all(isinstance(b, str) for b in beats):
            errors.append(f"第 {n} 章 beats 必须是字符串列表")
    return errors


def fixture_from_dict(data: dict[str, Any], *, default_name: str = "fixture") -> Fixture:
    known = {f.name for f in fields(Fixture)}
    payload = {k: v for k, v in data.items() if k in known}
    payload.setdefault("name", default_name)
    fx = Fixture(**payload)
    problems = validate_fixture(fx)
    if problems:
        raise ValueError(f"夹具「{fx.name}」不合格:\n- " + "\n- ".join(problems))
    return fx


def load_fixture(name_or_path: str | Path) -> Fixture:
    """按内置名或文件路径加载并校验。"""
    path = _fixture_path(name_or_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return fixture_from_dict(data, default_name=path.stem)


def seed_fixture(db: Session, fx: Fixture, *, user_id: int | None = None) -> Project:
    """把夹具灌成一个可直接 generate_chapter 的项目(架构 + 全部章节蓝图)。"""
    project = Project(
        user_id=user_id,
        title=TITLE_PREFIX + fx.title,
        topic=fx.topic,
        genre=fx.genre,
        target_chapters=fx.chapter_count,
        target_words_per_chapter=fx.target_words,
        global_tendency=dict(fx.global_tendency or {}),
        concept=fx.concept,
        dna=fx.dna,
        world_rules=(fx.world_rules or "").strip() or None,
        review_pass_threshold=fx.review_threshold,
        status="writing",
    )
    db.add(project)
    db.flush()
    save_architecture(
        db, project, ArchitectureResult(**{k: fx.architecture[k] for k in ARCH_FIELDS})
    )
    save_blueprint(db, project, [dict(o) for o in fx.outlines])
    db.commit()
    db.refresh(project)
    return project


def outline_to_dict(outline: Outline) -> dict[str, Any]:
    data: dict[str, Any] = {"chapter_number": outline.chapter_number}
    for key in OUTLINE_FIELDS:
        value = getattr(outline, key, None)
        if isinstance(value, list):
            data[key] = list(value)
        else:
            data[key] = value or ("" if key not in ("beats", "characters_involved", "key_items") else [])
    return data


def _slug(text: str) -> str:
    slug = re.sub(r"[^\w-]+", "_", text.strip()).strip("_")
    return slug or "fixture"


def export_project(
    db: Session, project_id: int, *, chapters: int | None = None, name: str | None = None
) -> dict[str, Any]:
    """把库里一本书的开书输入抽成夹具 dict(不含正文)。chapters 限制导出前 N 章蓝图。"""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在")
    arch = project.architecture
    if arch is None:
        raise ValueError(f"项目 {project_id} 还没有架构,导不成夹具")
    query = (
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    )
    outlines = [outline_to_dict(o) for o in query.all()]
    if chapters:
        outlines = outlines[:chapters]
    if not outlines:
        raise ValueError(f"项目 {project_id} 没有章节蓝图,导不成夹具")
    title = project.title.removeprefix(TITLE_PREFIX)
    data = Fixture(
        name=name or _slug(title),
        title=title,
        topic=project.topic or "",
        genre=project.genre or "",
        architecture={k: getattr(arch, k) or "" for k in ARCH_FIELDS},
        outlines=outlines,
        target_words=project.target_words_per_chapter or 2500,
        global_tendency=dict(project.global_tendency or {}),
        world_rules=project.world_rules or "",
        concept=project.concept,
        dna=project.dna,
        review_threshold=project.review_pass_threshold or 7,
        notes=f"从项目 #{project_id} 导出,前 {len(outlines)} 章蓝图",
    ).to_dict()
    # 导出即校验:把不合格的夹具留给下次 load 才炸,等于把问题往后推
    fixture_from_dict(data, default_name=data["name"])
    return data


def save_fixture(data: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
