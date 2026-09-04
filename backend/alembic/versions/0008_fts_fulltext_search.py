"""FTS5 全文检索:五张 trigram 虚表 + 触发器同步(评价第 4 条)

「全书搜一句台词/一个设定在哪」此前没有入口,只能翻页找。这里用 SQLite FTS5
的 trigram 分词器建倒排:
  · trigram 支持中文子串匹配(unicode61 对连续 CJK 不分词,基本不可用);
  · ≥3 字的查询走 MATCH 倒排,<3 字的查询降级为对虚表 content 列的 LIKE
    (数据量为单书规模,可接受);
  · 摘要不用 SQL snippet()(对 LIKE 路径不可用),由接口层在 Python 里裁剪。

同步策略:五张源表各挂 INSERT/UPDATE/DELETE 触发器,任何写路径(ORM/CLI/迁移)
都自动保持索引一致,不依赖应用层记得调用。虚表 rowid = 源表 id,触发器按 rowid
直接寻址,删除无需扫索引。

修改纪律:改这里必须同步改 app/api/search.py 的查询列名;新增可检索内容
加新虚表+触发器+接口分支。

Revision ID: c9f4a2d73b15
Revises: f2b9d4c61e08
Create Date: 2026-09-05 07:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c9f4a2d73b15'
down_revision: Union[str, None] = 'f2b9d4c61e08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 章节正文:优先索引定稿,空定稿回退草稿
CH_CONTENT_NEW = "COALESCE(NULLIF(NEW.final_content, ''), NULLIF(NEW.draft_content, ''), '')"
CH_CONTENT_ROW = "COALESCE(NULLIF(final_content, ''), NULLIF(draft_content, ''), '')"
CH_NONEMPTY_NEW = CH_CONTENT_NEW + " <> ''"
CH_NONEMPTY_ROW = CH_CONTENT_ROW + " <> ''"


def _fts_ddl() -> list[str]:
    """建五张虚表 + 各 3 个同步触发器 + 存量回填,顺序执行。"""
    stmts: list[str] = []

    stmts.append(f"""
CREATE VIRTUAL TABLE fts_chapters USING fts5(
    content, project_id UNINDEXED, chapter_number UNINDEXED, tokenize='trigram'
)""")
    stmts.append(f"""
CREATE TRIGGER trg_fts_chapters_ins AFTER INSERT ON chapters
WHEN {CH_NONEMPTY_NEW}
BEGIN
    INSERT INTO fts_chapters(rowid, content, project_id, chapter_number)
    VALUES (NEW.id, {CH_CONTENT_NEW}, NEW.project_id, NEW.chapter_number);
END""")
    stmts.append(f"""
CREATE TRIGGER trg_fts_chapters_upd AFTER UPDATE ON chapters
BEGIN
    DELETE FROM fts_chapters WHERE rowid = OLD.id;
    INSERT INTO fts_chapters(rowid, content, project_id, chapter_number)
    SELECT NEW.id, {CH_CONTENT_NEW}, NEW.project_id, NEW.chapter_number
    WHERE {CH_NONEMPTY_NEW};
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_chapters_del AFTER DELETE ON chapters
BEGIN
    DELETE FROM fts_chapters WHERE rowid = OLD.id;
END""")
    stmts.append(f"""
INSERT INTO fts_chapters(rowid, content, project_id, chapter_number)
SELECT id, {CH_CONTENT_ROW}, project_id, chapter_number
FROM chapters WHERE {CH_NONEMPTY_ROW}
""")

    stmts.append("""
CREATE VIRTUAL TABLE fts_outlines USING fts5(
    content, project_id UNINDEXED, chapter_number UNINDEXED, title UNINDEXED, tokenize='trigram'
)""")
    stmts.append("""
CREATE TRIGGER trg_fts_outlines_ins AFTER INSERT ON outlines
WHEN COALESCE(NEW.title, '') || COALESCE(NEW.summary, '') <> ''
BEGIN
    INSERT INTO fts_outlines(rowid, content, project_id, chapter_number, title)
    VALUES (NEW.id, COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.chapter_purpose, ''), NEW.project_id, NEW.chapter_number, NEW.title);
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_outlines_upd AFTER UPDATE ON outlines
BEGIN
    DELETE FROM fts_outlines WHERE rowid = OLD.id;
    INSERT INTO fts_outlines(rowid, content, project_id, chapter_number, title)
    SELECT NEW.id, COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.chapter_purpose, ''), NEW.project_id, NEW.chapter_number, NEW.title
    WHERE COALESCE(NEW.title, '') || COALESCE(NEW.summary, '') <> '';
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_outlines_del AFTER DELETE ON outlines
BEGIN
    DELETE FROM fts_outlines WHERE rowid = OLD.id;
END""")
    stmts.append("""
INSERT INTO fts_outlines(rowid, content, project_id, chapter_number, title)
SELECT id, COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(chapter_purpose, ''), project_id, chapter_number, title
FROM outlines WHERE COALESCE(title, '') || COALESCE(summary, '') <> ''
""")

    stmts.append("""
CREATE VIRTUAL TABLE fts_entities USING fts5(
    content, project_id UNINDEXED, name UNINDEXED, entity_type UNINDEXED, tokenize='trigram'
)""")
    # 注意:aliases 是 JSON 列,SQLite 里存的是 \uXXXX 转义文本,直接拼进索引搜不到
    # 中文。用 json_each 把别名逐个解出原文再拼接(json 无效时按空数组处理)。
    stmts.append("""
CREATE TRIGGER trg_fts_entities_ins AFTER INSERT ON entities
WHEN COALESCE(NEW.name, '') <> ''
BEGIN
    INSERT INTO fts_entities(rowid, content, project_id, name, entity_type)
    SELECT NEW.id, COALESCE(NEW.name, '') || ' ' || (SELECT COALESCE(group_concat(je.value, ' '), '') FROM json_each(CASE WHEN json_valid(COALESCE(NEW.aliases, '')) THEN NEW.aliases ELSE '[]' END) je), NEW.project_id, NEW.name, NEW.entity_type;
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_entities_upd AFTER UPDATE ON entities
BEGIN
    DELETE FROM fts_entities WHERE rowid = OLD.id;
    INSERT INTO fts_entities(rowid, content, project_id, name, entity_type)
    SELECT NEW.id, COALESCE(NEW.name, '') || ' ' || (SELECT COALESCE(group_concat(je.value, ' '), '') FROM json_each(CASE WHEN json_valid(COALESCE(NEW.aliases, '')) THEN NEW.aliases ELSE '[]' END) je), NEW.project_id, NEW.name, NEW.entity_type
    WHERE COALESCE(NEW.name, '') <> '';
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_entities_del AFTER DELETE ON entities
BEGIN
    DELETE FROM fts_entities WHERE rowid = OLD.id;
END""")
    stmts.append("""
INSERT INTO fts_entities(rowid, content, project_id, name, entity_type)
SELECT id, COALESCE(name, '') || ' ' || (SELECT COALESCE(group_concat(je.value, ' '), '') FROM json_each(CASE WHEN json_valid(COALESCE(aliases, '')) THEN aliases ELSE '[]' END) je), project_id, name, entity_type
FROM entities WHERE COALESCE(name, '') <> ''
""")

    stmts.append("""
CREATE VIRTUAL TABLE fts_facts USING fts5(
    content, project_id UNINDEXED, source_chapter UNINDEXED, entity_id UNINDEXED, tokenize='trigram'
)""")
    stmts.append("""
CREATE TRIGGER trg_fts_facts_ins AFTER INSERT ON facts
WHEN COALESCE(NEW.content, '') <> ''
BEGIN
    INSERT INTO fts_facts(rowid, content, project_id, source_chapter, entity_id)
    VALUES (NEW.id, COALESCE(NEW.content, ''), NEW.project_id, NEW.source_chapter, NEW.entity_id);
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_facts_upd AFTER UPDATE ON facts
BEGIN
    DELETE FROM fts_facts WHERE rowid = OLD.id;
    INSERT INTO fts_facts(rowid, content, project_id, source_chapter, entity_id)
    SELECT NEW.id, COALESCE(NEW.content, ''), NEW.project_id, NEW.source_chapter, NEW.entity_id
    WHERE COALESCE(NEW.content, '') <> '';
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_facts_del AFTER DELETE ON facts
BEGIN
    DELETE FROM fts_facts WHERE rowid = OLD.id;
END""")
    stmts.append("""
INSERT INTO fts_facts(rowid, content, project_id, source_chapter, entity_id)
SELECT id, COALESCE(content, ''), project_id, source_chapter, entity_id
FROM facts WHERE COALESCE(content, '') <> ''
""")

    stmts.append("""
CREATE VIRTUAL TABLE fts_foreshadowings USING fts5(
    content, project_id UNINDEXED, chapter_planted UNINDEXED, status UNINDEXED, tokenize='trigram'
)""")
    stmts.append("""
CREATE TRIGGER trg_fts_foreshadowings_ins AFTER INSERT ON foreshadowings
WHEN COALESCE(NEW.description, '') <> ''
BEGIN
    INSERT INTO fts_foreshadowings(rowid, content, project_id, chapter_planted, status)
    VALUES (NEW.id, COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.notes, ''), NEW.project_id, NEW.chapter_planted, NEW.status);
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_foreshadowings_upd AFTER UPDATE ON foreshadowings
BEGIN
    DELETE FROM fts_foreshadowings WHERE rowid = OLD.id;
    INSERT INTO fts_foreshadowings(rowid, content, project_id, chapter_planted, status)
    SELECT NEW.id, COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.notes, ''), NEW.project_id, NEW.chapter_planted, NEW.status
    WHERE COALESCE(NEW.description, '') <> '';
END""")
    stmts.append("""
CREATE TRIGGER trg_fts_foreshadowings_del AFTER DELETE ON foreshadowings
BEGIN
    DELETE FROM fts_foreshadowings WHERE rowid = OLD.id;
END""")
    stmts.append("""
INSERT INTO fts_foreshadowings(rowid, content, project_id, chapter_planted, status)
SELECT id, COALESCE(description, '') || ' ' || COALESCE(notes, ''), project_id, chapter_planted, status
FROM foreshadowings WHERE COALESCE(description, '') <> ''
""")

    return [s.strip() for s in stmts]


def upgrade() -> None:
    for stmt in _fts_ddl():
        op.execute(stmt)


def downgrade() -> None:
    for table, src in (
        ("fts_chapters", "chapters"),
        ("fts_outlines", "outlines"),
        ("fts_entities", "entities"),
        ("fts_facts", "facts"),
        ("fts_foreshadowings", "foreshadowings"),
    ):
        for evt in ("ins", "upd", "del"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_fts_{src}_{evt}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
