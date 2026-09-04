# app/engines/drama/common.py
# -*- coding: utf-8 -*-
"""漫剧引擎公共件:上下文拼装、LLM 输出裁剪、行序列化。

只依赖 db 模型与 LLM 适配器,不碰 HTTP 概念(照 polish 引擎的分层惯例)。

画风方向目录与 clip/coerce_int 这些**三条出片线共用**的件已挪进 `engines/media/`
(directions / text)——这里从那边导入、对内继续用同名符号(漫剧内部几十处调用不动)。
新代码请直接从 `media.*` 取:宣传片/情绪短片不是漫剧的下游,不该反向依赖这个模块
(`tests/test_engine_conventions.py` 会挡)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    Chapter,
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    Entity,
    Outline,
    Project,
)
from app.engines.media.directions import (  # noqa: F401  漫剧内部沿用这些名字
    VALID_DIRECTIONS,
    direction_directive,
    direction_label,
)
from app.engines.media.text import clip, coerce_int  # noqa: F401

# 改编模式 → 提示词里的说明文案
MODE_DESC = {
    "dialogue": "对白演绎(角色台词驱动画面,主流漫剧形态)",
    "narration": "口播解说(旁白讲故事,画面配图,解说漫形态)",
}
VALID_MODES = ("dialogue", "narration")

# 景别/运镜白名单(normalize 用,白名单外的值截短保留——LLM 偶尔写"大特写"也放行)
SHOT_TYPES = ("远景", "全景", "中景", "近景", "特写")
CAMERAS = ("固定", "推", "拉", "摇", "跟随", "环绕")


def concept_block(project: Project) -> str:
    """结构化概念六字段渲染成提示词块(与 api/submission 同格式,引擎层自持一份)。"""
    c = project.concept or {}
    if not isinstance(c, dict):
        return ""
    labels = {
        "logline": "一句话故事", "hook": "钩子", "twist": "反转",
        "protagonist": "主角", "conflict": "核心冲突", "setting": "世界观/设定",
    }
    lines = [f"  {labels[k]}:{c[k]}" for k in labels if c.get(k)]
    return "【故事概念】\n" + "\n".join(lines) + "\n" if lines else ""


def dna_block(project: Project) -> str:
    """本书基因(故事 DNA,作者的「定味锚」)渲染成提示词块。

    此前漫剧线完全看不到 project.dna——改编等于丢味。直接复用
    schemas/dna.render()(参照坐标/味道轴/必须有/绝不能有/vibe 的现成
    渲染口径),空 DNA → 空串,开书零影响。
    """
    from app.schemas.dna import coerce_dna

    dna = coerce_dna(project.dna)
    if dna.is_empty():
        return ""
    return "【本书基因(作者的定味锚,改编遵循)】\n" + dna.render() + "\n"


def profile_block(project: Project) -> str:
    """创作偏好档案(global_tendency._profile:文风/禁忌避雷/读者定位)块。

    「禁忌/避雷」对改编尤其值钱——作者整书级的「不要什么」应当约束漫剧的
    再创作环节。无档案 → 空串。
    """
    from app.engines.tendency.assembler import _PROFILE_KEY, _PROFILE_LABELS

    profile = (project.global_tendency or {}).get(_PROFILE_KEY)
    if not isinstance(profile, dict):
        return ""
    lines = [
        f"  {label}:{clip(str(profile.get(key) or ''), 200)}"
        for key, label in _PROFILE_LABELS
        if str(profile.get(key) or "").strip()
    ]
    return "【创作偏好档案(作者的整书主张,改编遵循)】\n" + "\n".join(lines) + "\n" if lines else ""


def book_block(project: Project) -> str:
    """书级资产块(dna + 创作偏好档案)合并收口——风格卡/方向推荐/切集共用。

    synopsis 有意不注入:concept 六字段已覆盖故事层,再加是重复;
    Relationship/KnowledgeState/世界规则也不整体进漫剧 prompt(会爆上下文窗,
    角色信息走 characters._entity_digest 的结构化摘要)。
    """
    return dna_block(project) + profile_block(project)


def style_memo_block(project: Project) -> str:
    if project.style_memo and project.style_memo.strip():
        return f"【文风备忘(小说侧,供气质参考)】{project.style_memo.strip()[:300]}\n"
    return ""


def approved_chapter_numbers(db: Session, project_id: int) -> list[int]:
    """已定稿(approved)的章号列表——漫剧工坊的准入门槛与章节范围数据源。"""
    rows = (
        db.query(Chapter.chapter_number)
        .filter(Chapter.project_id == project_id, Chapter.status == "approved")
        .order_by(Chapter.chapter_number)
        .all()
    )
    return [n for (n,) in rows]


def chapter_final_text(db: Session, project_id: int, chapter_number: int) -> str:
    """取某章定稿正文(approved 后正文在 final_content;兜底 draft_content)。"""
    row = (
        db.query(Chapter.final_content, Chapter.draft_content)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .order_by(Chapter.chapter_number)
        .first()
    )
    if not row:
        return ""
    return (row.final_content or row.draft_content or "").strip()


# =============== 集的源章号(支持「数章并一集」) ===============

def episode_source_chapters(ep: DramaEpisode) -> list[int]:
    """集的源章号列表(升序去重)。

    兼容老数据:source_chapters 为空时回落到单个 source_chapter——
    迁移会回填,但 job 里现建的对象/测试直接构造的行可能只有单值。
    """
    out: list[int] = []
    for raw in (ep.source_chapters or []):
        n = coerce_int(raw, 0, lo=0)
        if n > 0 and n not in out:
            out.append(n)
    if not out and ep.source_chapter:
        out = [ep.source_chapter]
    return sorted(out)


def source_chapter_label(ep: DramaEpisode) -> str:
    """源章号的人话标签:「第 3 章」/「第 3-5 章」(连续)/「第 3、7 章」(跳号)。"""
    nums = episode_source_chapters(ep)
    if not nums:
        return "未指定源章"
    if len(nums) == 1:
        return f"第 {nums[0]} 章"
    if nums[-1] - nums[0] == len(nums) - 1:
        return f"第 {nums[0]}-{nums[-1]} 章"
    return "第 " + "、".join(str(n) for n in nums) + " 章"


def chapters_final_text(
    db: Session, project_id: int, chapter_numbers: list[int], budget: int
) -> tuple[str, list[int]]:
    """多章正文拼接(带章号小标题),总量控制在 budget 字符内。

    并集的每一章都要进剧本上下文——只喂主章会把并进来的章静默丢掉。
    预算按章平分(至少 800 字/章,避免章多时每章都被砍成碎片)。
    超预算的章**头尾保留、中段省略**:开头是衔接上文的关键,结尾是卡点素材
    的来源——旧的从头截断会把章尾砍掉,剧本经常写不好结尾,根在这里。
    返回 (拼接文本, 真的有正文的章号)。
    """
    got: list[int] = []
    texts: list[str] = []
    per = max(800, budget // max(1, len(chapter_numbers)))
    for n in chapter_numbers:
        body = chapter_final_text(db, project_id, n)
        if not body:
            continue
        got.append(n)
        texts.append(f"—— 第 {n} 章 ——\n{_head_tail(body, per)}")
    return "\n\n".join(texts)[:budget], got


def _head_tail(body: str, keep: int) -> str:
    """超预算的文本保头尾去中段(头 60% 尾 40%,衔接与卡点各得其所)。"""
    if len(body) <= keep:
        return body
    head = keep * 6 // 10
    tail = keep - head
    return body[:head] + "\n……(中略)……\n" + body[-tail:]


# =============== 行 → dict 序列化(API 响应/导出共用) ===============

def style_card_dict(card: DramaStyleCard | None) -> dict | None:
    if card is None:
        return None
    return {
        "id": card.id,
        "direction": card.direction or "auto",
        "direction_label": direction_label(card.direction or "auto"),
        "style_name": card.style_name,
        "style_cn": card.style_cn,
        "style_en": card.style_en,
        "negative": card.negative,
        "ratio": card.ratio,
    }


def style_card(db: Session, project_id: int) -> DramaStyleCard | None:
    """项目的美术风格卡(1 项目 1 张)。序列化角色卡/分镜时都要它——粘贴版要画幅
    与负面词基座,取不到就退化成默认 9:16 无负面词。"""
    return (
        db.query(DramaStyleCard)
        .filter(DramaStyleCard.project_id == project_id)
        .first()
    )


def character_card_dict(card: DramaCharacterCard, style=None) -> dict:
    """角色卡序列化。style 给上时附带定妆照的「按平台粘贴版」(见 paste.py)。"""
    from app.engines.drama.gender import gender_conflict_note
    from app.engines.drama.paste import ref_sheet_paste  # 局部导入:避免模块循环

    return {
        "id": card.id,
        "entity_id": card.entity_id,
        "name": card.name,
        # 性别单列一栏:前端可一键改,改完「按性别重出」整卡重写(见 gender.py)
        "gender": card.gender or "",
        # 描述与标定的性别打架时的一句人话提示(不打架为空串)
        "gender_conflict": gender_conflict_note(card),
        "appearance_cn": card.appearance_cn,
        "appearance_en": card.appearance_en,
        "outfit_cn": card.outfit_cn,
        "voice_desc": card.voice_desc,
        "tts_hint": card.tts_hint,
        "reading_notes": card.reading_notes,
        "locked": card.locked,
        # 定妆照(人物一致性:先出参考图,再逐格引用)
        "ref_prompt_cn": card.ref_prompt_cn or "",
        "ref_prompt_en": card.ref_prompt_en or "",
        "ref_images": ref_image_list(card),
        # 音色参考音频(完整档对白链:indextts2 克隆原料;空=未传,对白格回退普通出片)
        "voice_ref": getattr(card, "voice_ref", "") or "",
        "ref_paste": ref_sheet_paste(card, style) if (card.ref_prompt_cn or "") else None,
    }


def ref_image_list(card: DramaCharacterCard) -> list[dict]:
    """定妆照条目清洗成 [{kind, src, note}](脏数据一律丢,前端不必设防)。"""
    return _image_items(card.ref_images)


def shot_asset_list(shot: DramaShot) -> list[dict]:
    """这一格挂着的静帧,同定妆照一套结构(老库没这一列 → 空列表)。"""
    return _image_items(getattr(shot, "assets", None))


def _image_items(raw) -> list[dict]:
    """上传/外链图条目的统一清洗:结构不对的一律丢掉。"""
    out: list[dict] = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not src or kind not in ("upload", "url"):
            continue
        out.append({"kind": kind, "src": src, "note": str(item.get("note") or "")[:100]})
    return out


def has_ref_image(card: DramaCharacterCard | None) -> bool:
    return bool(card is not None and ref_image_list(card))


def shot_refs_by_seq(shots: list, cards: list[DramaCharacterCard]) -> dict[int, list[str]]:
    """格号 → 该格**已有定妆照**的出场角色名(按出场顺序)。

    两处共用:导出手册的参考图指令行、视频段计划 r2v 变体的主体绑定
    (段级主体 = 段内各格的并集,video.clips_payload 负责并)。没有定妆照的
    角色不出现——免得提示用户去传不存在的图。
    """
    with_ref = {c.name for c in cards if has_ref_image(c)}
    return {
        s.seq: [n for n in (s.characters or []) if n in with_ref]
        for s in shots
    }


def scene_card_dict(card: DramaSceneCard) -> dict:
    return {
        "id": card.id,
        "name": card.name,
        "appearance_cn": card.appearance_cn,
        "appearance_en": card.appearance_en,
    }


def episode_dict(ep: DramaEpisode) -> dict:
    return {
        "id": ep.id,
        "ep_index": ep.ep_index,
        "title": ep.title,
        "source_chapter": ep.source_chapter,
        "source_chapters": episode_source_chapters(ep),
        "source_label": source_chapter_label(ep),
        "hook": ep.hook,
        "recap": ep.recap,
        "cliffhanger": ep.cliffhanger,
        # 本集重点(作者改编意图,可空;写剧本时高优先级注入)
        "focus": getattr(ep, "focus", "") or "",
        "mode": ep.mode,
        "duration_target_s": ep.duration_target_s,
        "script": ep.script or {},
        "status": ep.status,
    }


def shot_dict(shot: DramaShot, paste: dict | None = None, video: dict | None = None) -> dict:
    """分镜格序列化。paste = 生图粘贴版,video = 生视频粘贴版(见 shots_payload)。"""
    return {
        "id": shot.id,
        "episode_id": shot.episode_id,
        "seq": shot.seq,
        "scene_name": shot.scene_name,
        "characters": shot.characters or [],
        "action_desc": shot.action_desc,
        "shot_type": shot.shot_type,
        "camera": shot.camera,
        "dialogue": shot.dialogue,
        # 配音情绪(完整档对白链;空=平静,见 engines/render/emotion.py)
        "emotion": getattr(shot, "emotion", "") or "",
        "duration_s": shot.duration_s,
        "prompt_cn": shot.prompt_cn,
        "prompt_en": shot.prompt_en,
        "negative": shot.negative,
        # 运动轨:图生视频只吃这条(写外貌会让它重画脸,见 drama/video.py)
        "motion_cn": shot.motion_cn or "",
        "motion_en": shot.motion_en or "",
        # 施工进度:出好的静帧挂回这一格 + 两个打勾栏(几十格的手工活,靠脑子记必丢)
        "assets": shot_asset_list(shot),
        "clip_ref": getattr(shot, "clip_ref", "") or "",
        "done_still": bool(getattr(shot, "done_still", False)),
        "done_video": bool(getattr(shot, "done_video", False)),
        "paste": paste,
        "video_paste": video,
    }


def shot_progress(shots: list[DramaShot]) -> dict:
    """一集的施工进度:静帧/视频各做完几格,以及挂了几张素材。

    做成集级汇总而不是让前端自己数:导出手册、集详情、段计划三处都要用同一份口径。
    """
    return {
        "shots": len(shots),
        "stills_done": sum(1 for s in shots if getattr(s, "done_still", False)),
        "videos_done": sum(1 for s in shots if getattr(s, "done_video", False)),
        "assets": sum(len(shot_asset_list(s)) for s in shots),
    }


def shots_payload(db: Session, project_id: int, shots: list[DramaShot]) -> list[dict]:
    """一组分镜格 → 前端载荷(每格附「按平台粘贴版」:出图一套 + 生视频一套)。

    粘贴版在后端拼:导出手册与前端复制按钮共用同一套规则,不会两边各写一份跑偏。
    参考图指令只在该格出场角色**确实有定妆照**时才加,免得提示用户去传不存在的图。
    """
    from app.engines.drama.paste import shot_paste
    from app.engines.drama.video import shot_video_paste

    style = style_card(db, project_id)
    by_name, by_alias = character_anchor_maps(db, project_id)
    out: list[dict] = []
    for s in shots:
        refs: list[str] = []
        for name in (s.characters or []):
            card = match_character(str(name), by_name, by_alias)
            if has_ref_image(card) and card.name not in refs:
                refs.append(card.name)
        out.append(
            shot_dict(
                s,
                paste=shot_paste(s, style, refs),
                video=shot_video_paste(s, style, ref_names=refs),
            )
        )
    return out


# =============== 资产索引(prompt_render 按名匹配用) ===============

def character_anchor_maps(
    db: Session, project_id: int
) -> tuple[dict[str, DramaCharacterCard], dict[str, DramaCharacterCard]]:
    """返回 (名字→卡, 别名→卡) 两个映射。别名来自故事圣经 Entity.aliases,
    让 LLM 在分镜里用旧称呼/小名也能命中同一张角色卡。"""
    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project_id)
        .all()
    )
    by_name = {c.name: c for c in cards}
    by_alias: dict[str, DramaCharacterCard] = {}
    entity_ids = [c.entity_id for c in cards if c.entity_id]
    if entity_ids:
        entities = {
            e.id: e
            for e in db.query(Entity).filter(Entity.id.in_(entity_ids)).all()
        }
        for c in cards:
            e = entities.get(c.entity_id) if c.entity_id else None
            for alias in ((e.aliases if e else None) or []):
                a = str(alias).strip()
                if a:
                    by_alias.setdefault(a, c)
    return by_name, by_alias


def match_character(
    name: str,
    by_name: dict[str, DramaCharacterCard],
    by_alias: dict[str, DramaCharacterCard],
) -> DramaCharacterCard | None:
    n = name.strip()
    return by_name.get(n) or by_alias.get(n)


def scene_anchor_map(db: Session, project_id: int) -> dict[str, DramaSceneCard]:
    cards = (
        db.query(DramaSceneCard)
        .filter(DramaSceneCard.project_id == project_id)
        .all()
    )
    return {c.name: c for c in cards}


def outline_rows(db: Session, project_id: int, from_ch: int, to_ch: int) -> list[Outline]:
    return (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number >= from_ch,
            Outline.chapter_number <= to_ch,
        )
        .order_by(Outline.chapter_number)
        .all()
    )
