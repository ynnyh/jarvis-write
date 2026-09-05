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

from sqlalchemy import String, cast
from sqlalchemy.orm import Session

from app.db.models import Entity, Fact, KnowledgeState, Relationship

logger = logging.getLogger("jarvis-write.bible")

# 单章硬约束块最多注入的状态事实行数:长篇里同一批角色的历史事实会累积,
# 无上限会把 prompt 撑爆。超限时按重要度排序截断(critical 排最前、优先保留,
# critical 数量本身超限才会轮到被砍),minor 最先出局。
_MAX_FACT_LINES = 40

# 关系边的 relation 只留短标签:截断上限(字符)与分隔符集合。
# 模型偶尔无视 prompt 的短标签要求,把整段事件经过塞进 content——长句挂在
# 边上会污染人物卡与生成注入。这里做确定性兜底,不再多打一次模型;
# 完整描述始终保留在 facts 行(账本),边上只留标签。
_RELATION_LABEL_LIMIT = 20
_RELATION_SEPARATORS = "，,。;；、：:（）()「」\"'"


def short_relation_label(text: str, limit: int = _RELATION_LABEL_LIMIT) -> str:
    """把关系描述截成短标签:取首个分隔符前的短语,超限再硬截。"""
    t = (text or "").strip().strip("「」\"'")
    for sep in _RELATION_SEPARATORS:
        idx = t.find(sep)
        if idx > 0:
            t = t[:idx]
            break
    t = t.strip().strip("的了着。,，")
    if len(t) > limit:
        t = t[:limit]
    return t

# 资源类事实:由 ledger.py 单独渲染成「角色资源账本」(自带闭集红线与预算),
# 因此调用方注入硬约束块时会把这两类排除掉,避免同一条在 prompt 里出现两遍。
RESOURCE_FACT_TYPES = ("possession", "ability")


class BibleService:
    def __init__(self, db: Session, project_id: int):
        self.db = db
        self.project_id = project_id

    # ---------- 实体 ----------
    def find_entity(self, name: str) -> Entity | None:
        """按名字或别名找实体。

        精确名字走 SQL 索引;别名回退只加载有别名的实体子集(远小于全表)。
        """
        name = name.strip()
        if not name:
            return None
        # 优先精确匹配 name(SQL 索引命中)
        ent = (
            self.db.query(Entity)
            .filter(Entity.project_id == self.project_id, Entity.name == name)
            .first()
        )
        if ent:
            return ent
        # 别名回退:只加载有别名的实体(JSON 存储可能 unicode 转义,LIKE 不可靠)
        candidates = (
            self.db.query(Entity)
            .filter(
                Entity.project_id == self.project_id,
                Entity.aliases != "[]",
                Entity.aliases.isnot(None),
            )
            .all()
        )
        for e in candidates:
            if name in (e.aliases or []):
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
        if entity_names:
            ids = set()
            for name in entity_names:
                ent = self.find_entity(str(name))
                if ent:
                    ids.add(ent.id)
            if not ids:
                return []
            q = q.filter(Fact.entity_id.in_(ids))
        return q.all()

    def _find_replaced_fact(self, entity_id: int, replaces: str) -> Fact | None:
        """定位 replaces 指向的那条仍生效的旧事实(抽取要求抄原文,但常有细微出入)。

        原先只做 content 精确相等,于是模型多写一个括号补注("持有半块干粮(张三给的)")
        就收不了口——旧事实永久留在开区间,后续每章都把作废的事实当硬约束注入。
        三级匹配沿用 foreshadow._find_by_description 的口径,越往后越宽容但歧义不猜:
          1) 精确;2) 去空白后精确;3) 唯一子串命中才认账(≥4 字,已按实体收窄候选)。
        """
        rows = (
            self.db.query(Fact)
            .filter(
                Fact.project_id == self.project_id,
                Fact.entity_id == entity_id,
                Fact.valid_until.is_(None),
            )
            .all()
        )
        for f in rows:
            if f.content == replaces:
                return f
        norm = "".join(replaces.split())
        for f in rows:
            if "".join((f.content or "").split()) == norm:
                return f
        if len(norm) >= 4:
            hits = [
                f
                for f in rows
                if norm in "".join((f.content or "").split())
                or "".join((f.content or "").split()) in norm
            ]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                logger.info(
                    "replaces 模糊匹配到 %d 条,歧义不猜、旧事实不收口:%s",
                    len(hits), replaces,
                )
        return None

    def retired_entity_ids(self) -> set[int]:
        """本书已退场(retired=True)实体的 id 集合。

        退场后其事实一律不再注入生成/门禁 prompt(历史数据保留)。
        资源账本(ledger.py)与硬约束块共用这一口径,别各写一份。
        """
        return {
            row.id
            for row in self.db.query(Entity.id).filter(
                Entity.project_id == self.project_id,
                Entity.retired.is_(True),
            )
        }

    def entity_name(self, entity_id: int) -> str:
        ent = self.db.get(Entity, entity_id)
        return ent.name if ent else f"实体{entity_id}"

    def hard_constraints_block(
        self,
        chapter_number: int,
        entity_names: list[str] | None = None,
        exclude_types: tuple[str, ...] = (),
    ) -> str:
        """渲染 Prompt 硬约束块:涉及角色在当前章的状态事实 + 出场人物相互关系。

        已退场(retired=True)的实体及其事实一律不注入——
        作者退场某个人物后,后续生成不再受其状态约束;历史数据保留。
        关系行只在给了出场名单(entity_names)时注入,且仅注入
        双方都在名单内、当前有效的关系边,避免无关关系膨胀 prompt。
        exclude_types:按 fact_type 排除(调用方传 RESOURCE_FACT_TYPES,把持有/能力
        让给资源账本渲染),排除后仍无内容才回落到"暂无"提示语。
        """
        facts = self.query_facts_at(chapter_number, entity_names)
        if exclude_types:
            facts = [f for f in facts if f.fact_type not in exclude_types]
        retired_ids = self.retired_entity_ids()
        if retired_ids:
            facts = [f for f in facts if f.entity_id not in retired_ids]
        lines = []
        if facts:
            rank = {"critical": 0, "major": 1, "minor": 2}
            # 先按重要度全局排:超限截断时先砍 minor 再砍 major,critical 永不被砍
            facts.sort(key=lambda f: rank.get(f.importance, 1))
            if len(facts) > _MAX_FACT_LINES:
                logger.info(
                    "第%d章硬约束事实 %d 条超上限 %d,按重要度截断(critical 优先保留)",
                    chapter_number, len(facts), _MAX_FACT_LINES,
                )
                facts = facts[:_MAX_FACT_LINES]
            # 展示时再按实体聚合(同一角色的事实排在一起)
            facts.sort(key=lambda f: (f.entity_id, rank.get(f.importance, 1)))
            for f in facts:
                mark = "❗" if f.importance == "critical" else "·"
                lines.append(
                    f"{mark} {self.entity_name(f.entity_id)}:{f.content}"
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
                    f"· 关系: {self.entity_name(e.from_entity_id)}"
                    f"→{self.entity_name(e.to_entity_id)}: "
                    f"{e.relation}(自第{e.valid_from}章起)"
                )
        return lines

    def known_roster_block(self, chapter_number: int) -> str:
        """已登场角色名册(闭集约束),防「凭空冒出常驻角色」——如大院一直写「只有三人/
        空荡荡」,第 8 章却蹦出一个「每天伺候饮食起居」却从未登场的仆役。

        名册取本书当前**非退场的 character 实体**现查派生(不新增存储):章后抽取
        只把通过门禁的章的人物写进圣经,故生成/校验第 N 章时,名册恰是第 1..N-1 章
        已登场的人——新冒出的常驻角色必不在其中。

        规则对名册是否完备不敏感:核心禁令是「不得把一个从未登场的人写成早已常驻/
        素来相熟」,新人必须以初次登场方式引入。故早章名册稀疏时仍然有效。
        """
        rows = (
            self.db.query(Entity)
            .filter(
                Entity.project_id == self.project_id,
                Entity.entity_type == "character",
                Entity.retired.is_(False),
            )
            .order_by(Entity.id)
            .all()
        )
        names: list[str] = []
        for e in rows:
            label = e.name
            aliases = [a for a in (e.aliases or []) if a and a != e.name]
            if aliases:
                label += "(" + "、".join(aliases[:3]) + ")"
            names.append(label)
        if names:
            roster = "本书截至本章已登场/在册的角色:" + "、".join(names) + "。"
        else:
            roster = "本书目前尚无已登记的角色(开篇阶段)。"
        return (
            "【已登场角色名册(闭集约束·硬规则)】\n"
            + roster
            + "\n严禁凭空引入一个被写成「一直都在 / 每天伺候饮食起居 / 素来相熟 / "
            "府里的老人 / 早就认识」却在前文从未登场的常驻角色。确需新增人物时,必须是"
            "本章蓝图【涉及人物】点名的,并以「初次登场 / 新来 / 头一回见」的方式交代来历,"
            "绝不能假装他早已存在。尤其要与前文「人少 / 空荡 / 只有某几人 / 没有外人」的"
            "设定保持一致,不要无缘由地多出仆役、随从、邻居等常驻配角。"
        )

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
                old = self._find_replaced_fact(entity.id, replaces)
                if old:
                    old.valid_until = chapter_number - 1
                    stats["closed"] += 1
                else:
                    # 不静默:模型明说"这条取代了旧事实",却没落到任何一行上——
                    # 旧事实会永远挂在开区间里(干粮吃完三章了账本还写着"持有半块干粮"),
                    # 账本失真就是这么来的。留日志好定位是措辞出入还是模型编的。
                    logger.warning(
                        "第%d章 %s 的 replaces 未命中任何有效事实,旧事实未收口:%s",
                        chapter_number, ent_name, replaces,
                    )

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

            # 关系条目双写 relationships 表:facts 行保留完整描述(时间机兼容),
            # 结构化边只挂短标签(供人物卡与生成注入使用);
            # 标签归一后,模型换措辞重述同一关系也会被 upsert 幂等挡住,边不再抖动
            if (ch.get("fact_type") or "") == "relationship":
                other_name = (ch.get("other_entity") or "").strip()
                if other_name and other_name != ent_name:
                    other = self.get_or_create_entity(other_name)
                    if self._upsert_relationship(
                        chapter_number, entity, other, short_relation_label(content)
                    ):
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
        from sqlalchemy import or_, and_

        # 只查这对实体的开放边(SQL 条件,不全表扫描)
        same_pair = (
            self.db.query(Relationship)
            .filter(
                Relationship.project_id == self.project_id,
                Relationship.valid_until.is_(None),
                or_(
                    and_(Relationship.from_entity_id == a.id, Relationship.to_entity_id == b.id),
                    and_(Relationship.from_entity_id == b.id, Relationship.to_entity_id == a.id),
                ),
            )
            .all()
        )
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
