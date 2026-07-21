# app/engines/consistency/bible.py
# -*- coding: utf-8 -*-
"""时序故事圣经服务(借鉴 knowrite Temporal Truth DB)。

核心能力:
- query_facts_at(n):查"第 n 章时刻"的有效事实(valid_from<=n 且未失效)
- apply_extraction():把章后抽取结果写回圣经(新事实开区间,被取代的旧事实关区间)
- hard_constraints_block():把涉及角色的当前事实渲染成 Prompt 硬约束块
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Entity, Fact, KnowledgeState, Relationship

logger = logging.getLogger("jarvis-write.bible")


class BibleService:
    def __init__(self, db: Session, project_id: int):
        self.db = db
        self.project_id = project_id

    # ---------- 实体 ----------
    def find_entity(self, name: str) -> Entity | None:
        """按名字或别名找实体。"""
        name = name.strip()
        if not name:
            return None
        ents = (
            self.db.query(Entity)
            .filter(Entity.project_id == self.project_id)
            .all()
        )
        for e in ents:
            if e.name == name or name in (e.aliases or []):
                return e
        return None

    def get_or_create_entity(
        self, name: str, entity_type: str = "character", aliases: list | None = None
    ) -> Entity:
        ent = self.find_entity(name)
        if ent is None:
            ent = Entity(
                project_id=self.project_id,
                entity_type=entity_type,
                name=name.strip(),
                aliases=aliases or [],
                base_profile={},
            )
            self.db.add(ent)
            self.db.flush()
        return ent

    # ---------- 时序查询(系统心脏) ----------
    def query_facts_at(
        self, chapter_number: int, entity_names: list[str] | None = None
    ) -> list[Fact]:
        """第 chapter_number 章时刻的有效事实。

        有效 = valid_from <= n 且 (valid_until 为空 或 valid_until >= n)。
        """
        q = (
            self.db.query(Fact)
            .filter(
                Fact.project_id == self.project_id,
                Fact.valid_from <= chapter_number,
            )
            .filter(
                (Fact.valid_until.is_(None)) | (Fact.valid_until >= chapter_number)
            )
        )
        facts = q.all()
        if entity_names:
            ids = set()
            for name in entity_names:
                ent = self.find_entity(str(name))
                if ent:
                    ids.add(ent.id)
            facts = [f for f in facts if f.entity_id in ids]
        return facts

    def _entity_name(self, entity_id: int) -> str:
        ent = self.db.get(Entity, entity_id)
        return ent.name if ent else f"实体{entity_id}"

    def hard_constraints_block(
        self, chapter_number: int, entity_names: list[str] | None = None
    ) -> str:
        """渲染 Prompt 硬约束块:涉及角色在当前章的状态事实 + 出场人物相互关系。

        已退场(retired=True)的实体及其事实一律不注入——
        作者退场某个人物后,后续生成不再受其状态约束;历史数据保留。
        关系行只在给了出场名单(entity_names)时注入,且仅注入
        双方都在名单内、当前有效的关系边,避免无关关系膨胀 prompt。
        """
        facts = self.query_facts_at(chapter_number, entity_names)
        retired_ids = {
            row.id
            for row in self.db.query(Entity.id).filter(
                Entity.project_id == self.project_id,
                Entity.retired.is_(True),
            )
        }
        if retired_ids:
            facts = [f for f in facts if f.entity_id not in retired_ids]
        lines = []
        if facts:
            # critical 优先,同实体聚合
            facts.sort(key=lambda f: (f.entity_id, {"critical": 0, "major": 1, "minor": 2}.get(f.importance, 1)))
            for f in facts:
                mark = "❗" if f.importance == "critical" else "·"
                lines.append(
                    f"{mark} {self._entity_name(f.entity_id)}:{f.content}"
                    f"(自第{f.valid_from}章起)"
                )
        if entity_names:
            lines.extend(
                self._relationship_lines(chapter_number, entity_names, retired_ids)
            )
        if not lines:
            return "(暂无已登记的状态约束)"
        return "\n".join(lines)

    def _relationship_lines(
        self,
        chapter_number: int,
        entity_names: list[str],
        retired_ids: set[int],
    ) -> list[str]:
        """本章出场人物相互之间、当前有效的关系边,渲染为约束行。

        任一方退场或不在出场名单内的边不注入。
        """
        ids = set()
        for name in entity_names:
            ent = self.find_entity(str(name))
            if ent and ent.id not in retired_ids:
                ids.add(ent.id)
        if len(ids) < 2:
            return []
        edges = (
            self.db.query(Relationship)
            .filter(
                Relationship.project_id == self.project_id,
                Relationship.valid_from <= chapter_number,
            )
            .filter(
                (Relationship.valid_until.is_(None))
                | (Relationship.valid_until >= chapter_number)
            )
            .all()
        )
        lines = []
        for e in sorted(edges, key=lambda r: r.id):
            if e.from_entity_id in ids and e.to_entity_id in ids:
                lines.append(
                    f"· 关系: {self._entity_name(e.from_entity_id)}"
                    f"→{self._entity_name(e.to_entity_id)}: "
                    f"{e.relation}(自第{e.valid_from}章起)"
                )
        return lines

    # ---------- 写回 ----------
    def purge_chapter_extraction(self, chapter_number: int) -> dict:
        """撤销某章此前抽取的全部圣经写入(重写正文前调用,防记忆污染)。

        1. 删除该章事实关联的 knowledge_states(SQLite FK 默认不级联,手动删)
        2. 删除 source_chapter == n 的事实
        3. 重新打开被该章"取代"关闭的旧事实(valid_until == n-1 → NULL)
        4. 关系边同理:删除 valid_from == n 的新边,重开 valid_until == n-1 的旧边
           (relationships 无 source_chapter 字段,以 valid_from 充当来源章标记)
        """
        facts = (
            self.db.query(Fact)
            .filter(
                Fact.project_id == self.project_id,
                Fact.source_chapter == chapter_number,
            )
            .all()
        )
        fact_ids = [f.id for f in facts]
        removed_ks = 0
        if fact_ids:
            removed_ks = (
                self.db.query(KnowledgeState)
                .filter(KnowledgeState.fact_id.in_(fact_ids))
                .delete(synchronize_session=False)
            )
            for f in facts:
                self.db.delete(f)

        reopened = (
            self.db.query(Fact)
            .filter(
                Fact.project_id == self.project_id,
                Fact.valid_until == chapter_number - 1,
            )
            # fetch:同步内存中已加载的对象,避免后续读到旧值
            .update({Fact.valid_until: None}, synchronize_session="fetch")
        )

        removed_rels = (
            self.db.query(Relationship)
            .filter(
                Relationship.project_id == self.project_id,
                Relationship.valid_from == chapter_number,
            )
            .delete(synchronize_session=False)
        )
        reopened_rels = (
            self.db.query(Relationship)
            .filter(
                Relationship.project_id == self.project_id,
                Relationship.valid_until == chapter_number - 1,
            )
            .update({Relationship.valid_until: None}, synchronize_session="fetch")
        )
        self.db.flush()
        stats = {
            "facts_removed": len(fact_ids),
            "knowledge_removed": removed_ks,
            "facts_reopened": reopened,
            "relationships_removed": removed_rels,
            "relationships_reopened": reopened_rels,
        }
        logger.info("圣经清理(第%d章): %s", chapter_number, stats)
        return stats

    def apply_extraction(self, chapter_number: int, extraction: dict) -> dict:
        """把章后抽取结果写入圣经。返回统计。"""
        stats = {"entities": 0, "facts": 0, "closed": 0, "knowledge": 0, "relationships": 0}

        for ent in extraction.get("new_entities", []) or []:
            name = (ent.get("name") or "").strip()
            if name and self.find_entity(name) is None:
                self.get_or_create_entity(
                    name,
                    ent.get("entity_type") or "character",
                    ent.get("aliases") or [],
                )
                stats["entities"] += 1

        fact_by_content: dict[str, Fact] = {}
        for ch in extraction.get("fact_changes", []) or []:
            ent_name = (ch.get("entity") or "").strip()
            content = (ch.get("content") or "").strip()
            if not ent_name or not content:
                continue
            entity = self.get_or_create_entity(ent_name)

            # 关闭被取代的旧事实区间
            replaces = (ch.get("replaces") or "").strip() if ch.get("replaces") else ""
            if replaces:
                old = (
                    self.db.query(Fact)
                    .filter(
                        Fact.project_id == self.project_id,
                        Fact.entity_id == entity.id,
                        Fact.content == replaces,
                        Fact.valid_until.is_(None),
                    )
                    .first()
                )
                if old:
                    old.valid_until = chapter_number - 1
                    stats["closed"] += 1

            fact = Fact(
                project_id=self.project_id,
                entity_id=entity.id,
                fact_type=ch.get("fact_type") or "state",
                content=content,
                valid_from=chapter_number,
                valid_until=None,
                importance=ch.get("importance") or "major",
                source_chapter=chapter_number,
            )
            self.db.add(fact)
            self.db.flush()
            fact_by_content[content] = fact
            stats["facts"] += 1

            # 关系条目双写 relationships 表:facts 行保留(时间机兼容),
            # 结构化边供人物卡与生成注入使用
            if (ch.get("fact_type") or "") == "relationship":
                other_name = (ch.get("other_entity") or "").strip()
                if other_name and other_name != ent_name:
                    other = self.get_or_create_entity(other_name)
                    if self._upsert_relationship(chapter_number, entity, other, content):
                        stats["relationships"] += 1

        for ku in extraction.get("knowledge_updates", []) or []:
            fact_content = (ku.get("fact") or "").strip()
            fact = fact_by_content.get(fact_content)
            if fact is None:
                continue
            knower = (ku.get("knower") or "").strip() or "reader"
            self.db.add(
                KnowledgeState(
                    project_id=self.project_id,
                    fact_id=fact.id,
                    knower=knower,
                    known_from_chapter=chapter_number,
                    knower_state=ku.get("state") or "known",
                )
            )
            stats["knowledge"] += 1

        self.db.flush()
        logger.info("圣经写入(第%d章): %s", chapter_number, stats)
        return stats

    def _upsert_relationship(
        self, chapter_number: int, a: Entity, b: Entity, relation: str
    ) -> bool:
        """写一条关系边,同实体对(不分方向)的时序更新语义与 facts 对齐:

        同一对实体已有关闭区间外的旧边 → 关区间(valid_until = n-1),新边开区间;
        已存在相同 relation 的有效边 → 视为无变化,不重复落库。
        返回是否真正写了新边。
        """
        pair = {a.id, b.id}
        open_edges = (
            self.db.query(Relationship)
            .filter(
                Relationship.project_id == self.project_id,
                Relationship.valid_until.is_(None),
            )
            .all()
        )
        same_pair = [e for e in open_edges if {e.from_entity_id, e.to_entity_id} == pair]
        if any(e.relation == relation for e in same_pair):
            return False
        for e in same_pair:
            e.valid_until = chapter_number - 1
        self.db.add(
            Relationship(
                project_id=self.project_id,
                from_entity_id=a.id,
                to_entity_id=b.id,
                relation=relation,
                valid_from=chapter_number,
                valid_until=None,
            )
        )
        return True
