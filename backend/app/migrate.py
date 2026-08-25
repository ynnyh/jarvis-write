# app/migrate.py
# -*- coding: utf-8 -*-
"""启动迁移(阶段 8:多用户;阶段 9:后台管理)。

用的是 create_all 而非 Alembic,已存在的 SQLite 库不会自动补新列。
这里做幂等的轻量迁移:
1. 给旧表补 user_id 列(SQLite 支持 ADD COLUMN);
2. 给 users 表补 is_active 列(存量用户全部置为可用);
3. 建初始 admin 账号(用户名/密码来自配置);
4. 把无主(user_id 为空)的存量数据归到 admin 名下。

每次启动都跑,全部幂等——补过的列/建过的账号会跳过。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import engine, session_scope

logger = logging.getLogger("jarvis-write.migrate")

# 需要补 user_id 的旧表
_TABLES_NEEDING_USER = ("projects", "provider_settings", "llm_usage")

# 老 provider key → 迁移到新表时的默认显示名
_PROVIDER_DISPLAY_NAMES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI 兼容",
    "gemini": "Gemini",
}


def _column_exists(table: str, column: str) -> bool:
    insp = inspect(engine)
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:  # noqa: BLE001 — 表不存在等
        return False
    return column in cols


def _add_user_id_columns() -> None:
    """给旧表补 user_id 列(仅 SQLite / 幂等)。"""
    with engine.begin() as conn:
        for table in _TABLES_NEEDING_USER:
            insp = inspect(conn)
            if table not in insp.get_table_names():
                continue  # create_all 会新建,无需补列
            if not _column_exists(table, "user_id"):
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
                )
                logger.info("迁移:%s 补 user_id 列", table)


def _add_is_active_column() -> None:
    """阶段 9:给 users 表补 is_active 列(存量用户默认可用,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "users" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("users", "is_active"):
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN is_active BOOLEAN "
                    "NOT NULL DEFAULT 1"
                )
            )
            logger.info("迁移:users 补 is_active 列")


def _add_synopsis_column() -> None:
    """给 projects 表补 synopsis 列(书籍简介,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "synopsis"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN synopsis TEXT")
            )
            logger.info("迁移:projects 补 synopsis 列")


def _add_retired_column() -> None:
    """给 entities 表补 retired 列(人物退场标记,存量一律活跃,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "entities" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("entities", "retired"):
            conn.execute(
                text(
                    "ALTER TABLE entities ADD COLUMN retired BOOLEAN "
                    "NOT NULL DEFAULT 0"
                )
            )
            logger.info("迁移:entities 补 retired 列")


def _add_concept_column() -> None:
    """给 projects 表补 concept 列(结构化故事概念 JSON,幂等)。

    SQLite 的 JSON 底层是 TEXT;存量项目该列为 NULL,由灵感工坊逐步填充,
    架构生成在 concept 为空时回落到 topic 一句话(向后兼容)。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "concept"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN concept JSON")
            )
            logger.info("迁移:projects 补 concept 列")


def _add_dna_column() -> None:
    """给 projects 表补 dna 列(故事 DNA / 本书基因 JSON,幂等)。

    SQLite 的 JSON 底层是 TEXT;存量项目该列为 NULL,由灵感工坊坐标卡逐步填充,
    生成在 dna 为空时回落到题材边界软约束(向后兼容)。见 app/schemas/dna.py。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "dna"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN dna JSON")
            )
            logger.info("迁移:projects 补 dna 列")


def _add_canon_column() -> None:
    """给 projects 表补 canon 列(故事宪法:留白/常驻装置/倒计时 JSON,幂等)。

    SQLite 的 JSON 底层是 TEXT;存量项目该列为 NULL,由作者编辑 / LLM 建议逐步填充,
    生成与门禁在 canon 为空时行为与旧版一致(向后兼容)。见 app/schemas/canon.py。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "canon"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN canon JSON")
            )
            logger.info("迁移:projects 补 canon 列")


def _add_issue_payload_column() -> None:
    """给 chapter_issues 表补 payload 列(结构化载荷 JSON,幂等)。

    只有 source="canon"(LLM 提议的故事宪法建议)才在此存结构化提案
    {kind: absence|device|deadline, ...},供「采纳进宪法」端点无损重建 canon 条目;
    其它来源(gate/preflight/diag/review/rules)一律 NULL。存量记录该列为 NULL,
    读取方按 None 处理。见 app/engines/consistency/extractor.py 的 canon 建议落库。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "chapter_issues" not in insp.get_table_names():
            return  # create_all 会新建带 payload 的表,无需补列
        if not _column_exists("chapter_issues", "payload"):
            conn.execute(
                text("ALTER TABLE chapter_issues ADD COLUMN payload JSON")
            )
            logger.info("迁移:chapter_issues 补 payload 列")


def _add_setup_columns() -> None:
    """给 projects 表补 setup_state / chat_log 列(起步流 + 对话落库,幂等)。

    存量项目 setup_state 为 NULL = 起步已完成;chat_log NULL = 无对话记录。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        if not _column_exists("projects", "setup_state"):
            conn.execute(
                text("ALTER TABLE projects ADD COLUMN setup_state VARCHAR(20)")
            )
            logger.info("迁移:projects 补 setup_state 列")
        if not _column_exists("projects", "chat_log"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN chat_log JSON"))
            logger.info("迁移:projects 补 chat_log 列")
        if not _column_exists("projects", "macro_plan"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN macro_plan JSON"))
            logger.info("迁移:projects 补 macro_plan 列")


def _add_word_guard_columns() -> None:
    """给 projects 表补字数守卫配置列(幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        if not _column_exists("projects", "word_guard_enabled"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN word_guard_enabled "
                    "BOOLEAN NOT NULL DEFAULT 1"
                )
            )
            logger.info("迁移:projects 补 word_guard_enabled 列")
        if not _column_exists("projects", "word_guard_ratio"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN word_guard_ratio "
                    "REAL NOT NULL DEFAULT 1.5"
                )
            )
            logger.info("迁移:projects 补 word_guard_ratio 列")
        if not _column_exists("projects", "auto_split_enabled"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN auto_split_enabled "
                    "BOOLEAN NOT NULL DEFAULT 1"
                )
            )
            logger.info("迁移:projects 补 auto_split_enabled 列")


def _add_review_columns() -> None:
    """给 projects 表补编辑部审校把关配置列(幂等)。

    达标阈值默认 7(四维均需 >=),自动回炉默认开,回炉上限默认 3 轮。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        if not _column_exists("projects", "review_pass_threshold"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN review_pass_threshold "
                    "INTEGER NOT NULL DEFAULT 7"
                )
            )
            logger.info("迁移:projects 补 review_pass_threshold 列")
        if not _column_exists("projects", "review_auto_revise"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN review_auto_revise "
                    "BOOLEAN NOT NULL DEFAULT 1"
                )
            )
            logger.info("迁移:projects 补 review_auto_revise 列")
        if not _column_exists("projects", "review_max_revisions"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN review_max_revisions "
                    "INTEGER NOT NULL DEFAULT 3"
                )
            )
            logger.info("迁移:projects 补 review_max_revisions 列")


def _add_outline_beats_column() -> None:
    """给 outlines 表补 beats 列(章内节拍 JSON list,幂等)。

    存量章节该列为 NULL/空;draft prompt 在 beats 为空时回落到只用 summary
    (向后兼容);已有书可通过「重构翻新」补齐节拍。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "outlines" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("outlines", "beats"):
            conn.execute(text("ALTER TABLE outlines ADD COLUMN beats JSON"))
            logger.info("迁移:outlines 补 beats 列")


def _add_project_style_memo_column() -> None:
    """给 projects 表补 style_memo 列(文风备忘,随书累积,幂等)。

    存量项目该列为 NULL=尚未累积;逐章生成时增量更新并注入后续草稿,
    防长篇后段人物声音漂移、调性变淡。见 prompts/chapter.py。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        if not _column_exists("projects", "style_memo"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN style_memo TEXT"))
            logger.info("迁移:projects 补 style_memo 列")


def _add_chapter_review_snapshot_column() -> None:
    """给 chapters 表补主审结果快照列(幂等)。存量章节为空字符串=无快照。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "chapters" not in insp.get_table_names():
            return
        if not _column_exists("chapters", "review_snapshot"):
            conn.execute(
                text(
                    "ALTER TABLE chapters ADD COLUMN review_snapshot "
                    "TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("迁移:chapters 补 review_snapshot 列")


def _add_chapter_proofread_snapshot_column() -> None:
    """给 chapters 表补校对结果快照列(幂等)。存量章节为空字符串=无快照。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "chapters" not in insp.get_table_names():
            return
        if not _column_exists("chapters", "proofread_snapshot"):
            conn.execute(
                text(
                    "ALTER TABLE chapters ADD COLUMN proofread_snapshot "
                    "TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("迁移:chapters 补 proofread_snapshot 列")


def _add_world_rules_column() -> None:
    """给 projects 表补 world_rules 列(世界观硬规则钉板,每行一条,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        if not _column_exists("projects", "world_rules"):
            conn.execute(text("ALTER TABLE projects ADD COLUMN world_rules TEXT"))
            logger.info("迁移:projects 补 world_rules 列")


def _add_queue_require_approved_column() -> None:
    """给 projects 表补连写前置配置列(幂等)。默认 False=宽松(仅 quarantined 暂停)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        if not _column_exists("projects", "queue_require_approved"):
            conn.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN queue_require_approved "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            logger.info("迁移:projects 补 queue_require_approved 列")


def _migrate_finalized_to_approved() -> None:
    """状态机扩展(docs/08 §5.5):存量 finalized 章节一次性映射为 approved。

    用 PRAGMA user_version 做一次性标记(1→2):代码已不再写 finalized,
    老库的 finalized 等价于"已审过的成文章节",映射后行为不变。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "chapters" not in insp.get_table_names():
            return
        version = conn.execute(text("PRAGMA user_version")).scalar() or 0
        if version >= 2:
            return
        result = conn.execute(
            text("UPDATE chapters SET status = 'approved' WHERE status = 'finalized'")
        )
        conn.execute(text("PRAGMA user_version = 2"))
        logger.info(
            "迁移:存量 finalized 章节映射为 approved 共 %d 行(user_version 1→2)",
            result.rowcount,
        )


def _disable_word_guard_default() -> None:
    """一次性把存量项目的字数守卫关掉(此前无 UI,全是默认开启,无人主动开过)。

    用 SQLite PRAGMA user_version 做一次性标记:只在版本 0→1 时执行,
    之后用户手动打开守卫也不会被重启覆盖。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "projects" not in insp.get_table_names():
            return
        version = conn.execute(text("PRAGMA user_version")).scalar() or 0
        if version >= 1:
            return
        conn.execute(
            text("UPDATE projects SET word_guard_enabled = 0, auto_split_enabled = 0")
        )
        conn.execute(text("PRAGMA user_version = 1"))
        logger.info("迁移:存量项目字数守卫统一关闭(user_version 0→1)")


def _ensure_admin(db: Session) -> User:
    settings = get_settings()
    admin = (
        db.query(User).filter(User.username == settings.admin_username).first()
    )
    if admin is None:
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            is_admin=True,
        )
        db.add(admin)
        db.flush()
        logger.info("迁移:创建初始管理员 %s", settings.admin_username)
        # 还在用代码里的默认密码:仅适合本地开发,务必提醒改掉
        if settings.admin_password == Settings.model_fields["admin_password"].default:
            logger.warning(
                "初始管理员 %s 使用的是默认密码,仅限本地开发;"
                "部署请通过环境变量 ADMIN_PASSWORD 设置强密码,或登录后立即修改",
                settings.admin_username,
            )
    return admin


def _claim_orphans(db: Session, admin_id: int) -> None:
    """把 user_id 为空的存量数据归到 admin。"""
    for table in _TABLES_NEEDING_USER:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            continue
        result = db.execute(
            text(
                f"UPDATE {table} SET user_id = :uid "
                "WHERE user_id IS NULL"
            ),
            {"uid": admin_id},
        )
        if result.rowcount:
            logger.info("迁移:%s 归属 admin 共 %d 行", table, result.rowcount)


def _migrate_provider_settings_to_configs() -> None:
    """老表 provider_settings → 新表 provider_configs(每用户一次性,幂等)。

    cc-switch 风格改造:每用户每协议一行升级为多套命名配置。
    判定方式:某协议配置已存在于 provider_configs 则跳过该行;
    api_key 已是密文,原样拷贝即可。老表保留不删,便于回滚排查。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        tables = insp.get_table_names()
        if "provider_settings" not in tables or "provider_configs" not in tables:
            return  # 老库没新表由 create_all 先建;全新库没老表无事可做
        rows = conn.execute(
            text(
                "SELECT user_id, provider, api_key, base_url, model, is_default "
                "FROM provider_settings"
            )
        ).fetchall()
        migrated = 0
        for user_id, provider, api_key, base_url, model, is_default in rows:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM provider_configs "
                    "WHERE user_id = :u AND interface_format = :f LIMIT 1"
                ),
                {"u": user_id, "f": provider},
            ).first()
            if exists:
                continue
            conn.execute(
                text(
                    "INSERT INTO provider_configs "
                    "(user_id, name, interface_format, api_key, base_url, model, "
                    " timeout, max_tokens, is_default, is_default_fast, "
                    " created_at, updated_at) "
                    "VALUES (:u, :n, :f, :k, :b, :m, 0, 0, :d, 0, "
                    "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "u": user_id,
                    "n": _PROVIDER_DISPLAY_NAMES.get(provider, provider),
                    "f": provider,
                    "k": api_key,
                    "b": base_url or "",
                    "m": model or "",
                    "d": bool(is_default),
                },
            )
            migrated += 1
        if migrated:
            logger.info(
                "迁移:provider_settings → provider_configs 共 %d 行", migrated
            )


def _encrypt_existing_keys() -> None:
    """把 provider_settings 里历史明文 api_key 加密回写(幂等:已加密的跳过)。

    key 加密上线前存的是明文;上线后新写的带 ENC_PREFIX。这里把存量明文补加密,
    之后 factory._db_settings 统一解密即可(见 app/crypto.py)。
    """
    from app.crypto import ENC_PREFIX, encrypt

    with engine.begin() as conn:
        insp = inspect(conn)
        if "provider_settings" not in insp.get_table_names():
            return
        rows = conn.execute(
            text("SELECT id, api_key FROM provider_settings")
        ).fetchall()
        migrated = 0
        for row_id, api_key in rows:
            if api_key and not api_key.startswith(ENC_PREFIX):
                conn.execute(
                    text("UPDATE provider_settings SET api_key = :k WHERE id = :i"),
                    {"k": encrypt(api_key), "i": row_id},
                )
                migrated += 1
        if migrated:
            logger.info("迁移:%d 条历史明文 LLM key 已加密", migrated)


def _add_drama_voice_columns() -> None:
    """漫剧工坊阶段 2:drama_character_cards 补 tts_hint / reading_notes 列(幂等)。

    新表(drama_production_packs 等)由 create_all 建;只有老分支装过
    drama 表的库需要补这两列。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_character_cards" not in insp.get_table_names():
            return  # create_all 会新建,无需补列
        for col in ("tts_hint", "reading_notes"):
            if not _column_exists("drama_character_cards", col):
                conn.execute(
                    text(f"ALTER TABLE drama_character_cards ADD COLUMN {col} TEXT")
                )
                logger.info("迁移:drama_character_cards 补 %s 列", col)


def _add_drama_style_direction_column() -> None:
    """漫剧工坊:drama_style_cards 补 direction 列(画风方向,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_style_cards" not in insp.get_table_names():
            return
        if not _column_exists("drama_style_cards", "direction"):
            conn.execute(
                text("ALTER TABLE drama_style_cards ADD COLUMN direction VARCHAR(40) DEFAULT 'auto'")
            )
            logger.info("迁移:drama_style_cards 补 direction 列")


def _add_promo_chunks_column() -> None:
    """宣传片工坊:promo_plans 补 chunks 列(生成切段,幂等)。"""
    with engine.begin() as conn:
        insp = inspect(conn)
        if "promo_plans" not in insp.get_table_names():
            return
        if not _column_exists("promo_plans", "chunks"):
            conn.execute(text("ALTER TABLE promo_plans ADD COLUMN chunks JSON"))
            logger.info("迁移:promo_plans 补 chunks 列")


def _add_drama_episode_source_chapters_column() -> None:
    """漫剧工坊:drama_episodes 补 source_chapters 列(多章并一集,幂等)。

    老库每集只有单个 source_chapter,回填成 [source_chapter] —— 语义等价,
    之后「数章并一集」才有地方存(见 models/drama.py 的字段注释)。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_episodes" not in insp.get_table_names():
            return
        if not _column_exists("drama_episodes", "source_chapters"):
            conn.execute(text("ALTER TABLE drama_episodes ADD COLUMN source_chapters JSON"))
            logger.info("迁移:drama_episodes 补 source_chapters 列")
        # 回填(新加列或历史遗留的空值都补上;SQLite 的 JSON 列底层是 TEXT)
        conn.execute(
            text(
                "UPDATE drama_episodes SET source_chapters = "
                "'[' || CAST(source_chapter AS TEXT) || ']' "
                "WHERE (source_chapters IS NULL OR source_chapters IN ('', '[]')) "
                "AND source_chapter > 0"
            )
        )


def _add_drama_ref_sheet_columns() -> None:
    """漫剧工坊:drama_character_cards 补定妆照三列(幂等)。

    ref_prompt_cn/ref_prompt_en = 定妆照提示词;ref_images = 定妆照资产列表(JSON)。
    人物一致性靠「先出一张定妆照当参考图」,这三列是它的落点。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_character_cards" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        for col, ddl in (
            ("ref_prompt_cn", "TEXT"),
            ("ref_prompt_en", "TEXT"),
            ("ref_images", "JSON"),
        ):
            if not _column_exists("drama_character_cards", col):
                conn.execute(
                    text(f"ALTER TABLE drama_character_cards ADD COLUMN {col} {ddl}")
                )
                logger.info("迁移:drama_character_cards 补 %s 列", col)


def _add_drama_gender_column() -> None:
    """漫剧工坊:drama_character_cards 补 gender 列(幂等)。

    老库的角色卡没有这一列,性别只散落在外貌段的自由文本里——改不动也校不了。
    补上之后:生成时当硬约束下发、用户可一键改、描述打架能提示(见 drama/gender.py)。
    存量行留空 = 「未定」,下次生成或用户拍板时自然填上。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_character_cards" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        if not _column_exists("drama_character_cards", "gender"):
            conn.execute(
                text(
                    "ALTER TABLE drama_character_cards "
                    "ADD COLUMN gender VARCHAR(10) DEFAULT ''"
                )
            )
            logger.info("迁移:drama_character_cards 补 gender 列")


def _add_drama_motion_columns() -> None:
    """漫剧工坊:drama_shots 补 motion_cn / motion_en 两列(幂等)。

    生图提示词直接拿去生视频是错的:图生视频的首帧已经把长相钉死,提示词里再
    描述外貌会让模型把脸重画一遍。所以运动轨单列两栏,只写「怎么动」
    (见 engines/drama/video.py)。存量行留空 → 引擎按运镜栏兜底拼一条,不影响可用。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_shots" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        for col in ("motion_cn", "motion_en"):
            if not _column_exists("drama_shots", col):
                conn.execute(text(f"ALTER TABLE drama_shots ADD COLUMN {col} TEXT DEFAULT ''"))
                logger.info("迁移:drama_shots 补 %s 列", col)


def _add_drama_shot_asset_columns() -> None:
    """漫剧工坊:drama_shots 补「挂素材 + 打勾」四列(幂等)。

    assets = 挂回这一格的静帧([{kind,src,note}] JSON);clip_ref = 成片在哪(外链/文件名);
    done_still / done_video = 这一格出图、生视频做完没有。一集几十格的手工活,没有
    进度栏必然做丢或重做——这四列是那份「逐格施工单」的落点。
    存量行:assets 留 NULL(读取一律走 common.shot_asset_list,当空列表处理),
    打勾列默认 0(未做),不影响任何既有功能。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "drama_shots" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        for col, ddl in (
            ("assets", "JSON"),
            ("clip_ref", "VARCHAR(500) DEFAULT ''"),
            ("done_still", "BOOLEAN DEFAULT 0"),
            ("done_video", "BOOLEAN DEFAULT 0"),
        ):
            if not _column_exists("drama_shots", col):
                conn.execute(text(f"ALTER TABLE drama_shots ADD COLUMN {col} {ddl}"))
                logger.info("迁移:drama_shots 补 %s 列", col)


def _add_provider_thinking_mode_column() -> None:
    """provider_configs 补 thinking_mode 列(幂等)。

    V4 系模型(deepseek-v4-flash 等)思考默认开且 effort=high,结构化长契约
    (分镜/蓝图/提取的 JSON)会触发数万 token 思考吃光 max_tokens → 空正文 +
    翻倍重试分钟级白跑。全局默认改为关思考,这列给「某套配置要强制开思考」
    留按配置覆盖的口子(low/high/max)。空串 = 跟随全局默认。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "provider_configs" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        if not _column_exists("provider_configs", "thinking_mode"):
            conn.execute(
                text("ALTER TABLE provider_configs ADD COLUMN thinking_mode VARCHAR(10) DEFAULT ''")
            )
            logger.info("迁移:provider_configs 补 thinking_mode 列")


def _add_mood_clip_steering_columns() -> None:
    """mood_clips 补四个导向维度列(幂等)。

    用户反馈"生成内容总不满意、方向太粗":只有主题+画风两维可调。
    新增台词风格/节奏/情绪浓度三档下拉 + 氛围关键词自由文本,全部默认
    auto/空(存量行为零变化),注入两段式提示词做硬约束。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "mood_clips" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        for col, ddl in (
            ("dialogue_style", "VARCHAR(20) DEFAULT 'auto'"),
            ("pacing", "VARCHAR(20) DEFAULT 'auto'"),
            ("intensity", "VARCHAR(20) DEFAULT 'auto'"),
            ("style_hints", "VARCHAR(160) DEFAULT ''"),
        ):
            if not _column_exists("mood_clips", col):
                conn.execute(text(f"ALTER TABLE mood_clips ADD COLUMN {col} {ddl}"))
                logger.info("迁移:mood_clips 补 %s 列", col)


def _add_mood_clip_mode_column() -> None:
    """灵感工坊:mood_clips 补 mode 列(工坊类型,幂等)。

    mood=情绪短片(默认),play=灵感工坊(玩法命题)。存量行默认 mood,行为零变化。
    """
    with engine.begin() as conn:
        insp = inspect(conn)
        if "mood_clips" not in insp.get_table_names():
            return  # create_all 会按新模型建表,无需补列
        if not _column_exists("mood_clips", "mode"):
            conn.execute(
                text("ALTER TABLE mood_clips ADD COLUMN mode VARCHAR(20) NOT NULL DEFAULT 'mood'")
            )
            logger.info("迁移:mood_clips 补 mode 列")


def run_migrations() -> None:
    """启动时调用。幂等。"""
    _add_user_id_columns()
    _add_is_active_column()
    _add_synopsis_column()
    _add_concept_column()
    _add_dna_column()
    _add_canon_column()
    _add_issue_payload_column()
    _add_setup_columns()
    _add_retired_column()
    _add_word_guard_columns()
    _add_review_columns()
    _add_outline_beats_column()
    _add_project_style_memo_column()
    _add_world_rules_column()
    _add_chapter_review_snapshot_column()
    _add_chapter_proofread_snapshot_column()
    _add_queue_require_approved_column()
    _add_drama_voice_columns()
    _add_drama_style_direction_column()
    _add_promo_chunks_column()
    _add_drama_episode_source_chapters_column()
    _add_drama_ref_sheet_columns()
    _add_drama_gender_column()
    _add_drama_motion_columns()
    _add_drama_shot_asset_columns()
    _add_provider_thinking_mode_column()
    _add_mood_clip_steering_columns()
    _add_mood_clip_mode_column()
    _disable_word_guard_default()
    _migrate_finalized_to_approved()
    # 先补加密老表存量明文 key,再拷到新表,保证 provider_configs 落库必为密文
    _encrypt_existing_keys()
    _migrate_provider_settings_to_configs()
    with session_scope() as db:
        admin = _ensure_admin(db)
        db.flush()
        _claim_orphans(db, admin.id)
