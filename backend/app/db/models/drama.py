# app/db/models/drama.py
"""漫剧工坊:把已定稿小说改编成漫剧「拍摄手册」(集 → 分镜 → 三轨提示词)。

设计原则(对标 LumenX / Toonflow / AnimaHub,取长补短):
- 资产先行:风格卡/角色卡/场景卡是资产层,分镜提示词由资产锚段拼装注入,
  全片画风与角色形象统一不靠抽卡运气(LumenX 可控美术指导)。
- 衍生消费:只读小说已有资产(章节/故事圣经/蓝图),不反向影响写作主链;
  准入门槛 = 有已定稿章节(AI-Novel-Writing-Assistant 衍生工坊理念)。
- 角色卡可锁定:locked 后批量重跑不覆盖,人工调过的形象不丢。
- 只产提示词:不接图像/TTS/视频模型,产物是结构化手册,拿去即梦/可灵/剪映出片。

分镜与资产用「名字」关联(角色名/场景名)而非外键 ID:LLM 输出天然是名字,
渲染提示词时按名(含别名)匹配资产卡,匹不上的照常出片只是少注入锚段,
不会因 ID 解析失败而整集卡住。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class DramaStyleCard(Base, TimestampMixin):
    """项目级美术风格卡(1 项目 1 张):画风锁定段注入每个分镜提示词。"""

    __tablename__ = "drama_style_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    style_name: Mapped[str] = mapped_column(String(60), default="")
    # 画风方向(auto/comic_cn/anime_jp/render3d/live/ink_wash/cyber,见 common.DRAMA_DIRECTIONS):
    # 用户显式选定的艺术方向,风格卡生成时的硬约束——动画系为默认推荐,
    # 真人写实保留但前端挂「恐怖谷/一致性更难」提示
    direction: Mapped[str] = mapped_column(String(40), default="auto")
    # 画风锁定段(中文):媒介/笔触/色彩/光影/质感,60-120 字,逐字注入每条分镜提示词
    style_cn: Mapped[str] = mapped_column(Text, default="")
    # 画风锁定段(英文关键词串,MJ/即梦国际版用)
    style_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    # 画幅,默认竖屏短剧
    ratio: Mapped[str] = mapped_column(String(10), default="9:16")


class DramaCharacterCard(Base, TimestampMixin):
    """角色视觉卡:锁定外貌描述段,分镜按出场角色名注入,保人物一致性。

    locked=True 表示用户人工确认/修改过,批量重新生成时跳过不覆盖。
    """

    __tablename__ = "drama_character_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # 关联故事圣经实体(可空:允许用户手加角色卡)
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # 性别(""=未定 / female / male / other):从自由文本里拎出来单独存的一列。
    # 写错性别是漫剧最刺眼的错(女角色出成男相),而且会顺着外貌段 → 定妆照 →
    # 每格提示词 → 配音声线一路复制。单列一栏才能:生成时当硬约束下发、
    # 用户一键改、事后校验描述有没有跟它打架(见 engines/drama/gender.py)。
    gender: Mapped[str] = mapped_column(String(10), default="")
    # 锁定外貌段(中文):发型面容/体型/服饰材质颜色,80-150 字,必须「可画」
    appearance_cn: Mapped[str] = mapped_column(Text, default="")
    appearance_en: Mapped[str] = mapped_column(Text, default="")
    outfit_cn: Mapped[str] = mapped_column(Text, default="")
    # TTS 声线描述(性别/年龄段/音色/语气),阶段 2 配音稿直接用
    voice_desc: Mapped[str] = mapped_column(Text, default="")
    # 各 TTS 平台的选型建议(火山/MiniMax/剪映朗读怎么挑音色,阶段 2 声线选型卡)
    tts_hint: Mapped[str] = mapped_column(Text, default="")
    # 朗读备注:语速/情绪基调/重音,给配音环节的演奏指示
    reading_notes: Mapped[str] = mapped_column(Text, default="")
    # 定妆照(角色参考图)提示词:拿去生图站先出一张「正面半身+纯背景」的参考图,
    # 之后每格改用「参考图 + 本格提示词」出图,人物一致性才能从文字层落到像素层。
    # 与 appearance_cn 的分工:那段是注入每格的锁定外貌,这段是能独立出图的完整提示词
    # (含构图/光线/背景 + 画风锚)。
    ref_prompt_cn: Mapped[str] = mapped_column(Text, default="")
    ref_prompt_en: Mapped[str] = mapped_column(Text, default="")
    # 定妆照资产:[{kind: "upload"|"url", src: 相对路径或外链, note: 备注}],最多 3 张。
    # upload 的 src 存相对路径(相对上传根目录),换卷/搬迁不破;url 是用户贴的外链
    # (平台链接可能失效,所以上传优先)。
    ref_images: Mapped[Any] = mapped_column(JSON, default=list)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_drama_char_name"),)


class DramaSceneCard(Base, TimestampMixin):
    """场景定调卡:反复出现的场景一张卡,画面基调跨集统一。"""

    __tablename__ = "drama_scene_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    appearance_cn: Mapped[str] = mapped_column(Text, default="")
    appearance_en: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_drama_scene_name"),)


class DramaEpisode(Base, TimestampMixin):
    """一集(60-180 秒竖屏漫剧):由已定稿章节改编。

    status 流转:planned(切分完成)→ scripted(有剧本)→ storyboarded(有分镜)
    → ready(提示词已出,可导出拍摄手册)。
    script 为 JSON:{mode, synopsis, lines:[{speaker, text, action}]};
    重新规划会整表覆盖(删旧集与分镜再重建),属预期行为。
    """

    __tablename__ = "drama_episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    ep_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    # 主源章号(存章号而非 chapter.id:章节重生成不换号,引用更稳)。
    # = source_chapters 的最小章号,重规划的范围替换与集排序都按它走。
    source_chapter: Mapped[int] = mapped_column(Integer, default=0)
    # 源章号全集:一集可以由数章合并而来(EPISODE_PLAN_PROMPT 允许「过渡章数章并一集」),
    # 写剧本时按这个列表逐章取正文——只认 source_chapter 会把并进来的章静默丢掉。
    # 老库无此列时迁移回填 [source_chapter];读取一律走 common.episode_source_chapters。
    source_chapters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 前 3 秒钩子:第一画面/第一句话,刷到就停住的那种
    hook: Mapped[str] = mapped_column(Text, default="")
    # 本集梗概(50 字内,列表页速览)
    recap: Mapped[str] = mapped_column(Text, default="")
    # 结尾悬念:卡点钩子,逼观众点下一集
    cliffhanger: Mapped[str] = mapped_column(Text, default="")
    # dialogue(角色对白演绎,主流漫剧)/ narration(口播解说,画面配图)
    mode: Mapped[str] = mapped_column(String(20), default="dialogue")
    duration_target_s: Mapped[int] = mapped_column(Integer, default=90)
    script: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="planned")

    __table_args__ = (UniqueConstraint("project_id", "ep_index", name="uq_drama_ep_index"),)


class DramaShot(Base, TimestampMixin):
    """一个分镜:画面/景别/运镜/台词/时长 + 三轨提示词(中文六层/英文/负面)。

    characters 存出场角色名列表(JSON),scene_name 存场景名——渲染提示词时
    按名匹配角色卡/场景卡注入锚段(见 models/drama.py 模块注释)。
    """

    __tablename__ = "drama_shots"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("drama_episodes.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    scene_name: Mapped[str] = mapped_column(String(200), default="")
    characters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 这一格画面在干什么:动作/表情/互动,必须「可画」不写心理独白
    action_desc: Mapped[str] = mapped_column(Text, default="")
    # 景别:远景/全景/中景/近景/特写
    shot_type: Mapped[str] = mapped_column(String(20), default="")
    # 运镜:固定/推/拉/摇/跟随/环绕
    camera: Mapped[str] = mapped_column(String(20), default="")
    # 该镜头承载的台词或旁白(与剧本 lines 对齐,可空)
    dialogue: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[int] = mapped_column(Integer, default=4)
    # ===== 三轨提示词(拿去即梦/可灵/MJ)=====
    prompt_cn: Mapped[str] = mapped_column(Text, default="")
    prompt_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")


class DramaProductionPack(Base, TimestampMixin):
    """成片包(阶段 2):一集的配音稿 + 剪辑清单,由分镜/剧本/角色卡组装。

    pack 为 JSON,结构见 engines/drama/production.py:
    {mode, dubbing:[{seq,speaker,voice,tts_hint,text,tts_text,est_s,shot_duration_s}],
     narration_full, checklist:[{seq,scene,duration_s,subtitle,transition,bgm_tag,note}],
     totals:{shots,target_s,storyboard_s,voice_s}}
    重建即覆盖(upsert by episode_id)。
    """

    __tablename__ = "drama_production_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("drama_episodes.id", ondelete="CASCADE"), unique=True, index=True
    )
    pack: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DramaTrailer(Base, TimestampMixin):
    """预告片:从已有各集的高能素材重剪一条 30-60 秒宣传片(项目级,一条,重建覆盖)。

    lines 为 [{speaker, text}](旁白 + 金句对白,按预告片节奏);
    shots 为 [{seq, source_ep, scene_name, characters, action_desc, shot_type,
    camera, dialogue, duration_s, prompt_cn, prompt_en, negative}]——
    source_ep 标参考来源集号(0 = 预告片新创镜头),提示词同分镜三轨、
    锚段注入规则一致(画风/角色锚逐字嵌入 + 引擎兜底)。
    """

    __tablename__ = "drama_trailers"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    target_s: Mapped[int] = mapped_column(Integer, default=45)
    title: Mapped[str] = mapped_column(String(200), default="")
    lines: Mapped[list[Any]] = mapped_column(JSON, default=list)
    shots: Mapped[list[Any]] = mapped_column(JSON, default=list)
