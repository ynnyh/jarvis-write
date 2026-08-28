# app/db/models/promo.py
"""宣传片工坊:主题企划(城市/景区/品牌)→ 研讨对话 → 创作简报 → 解说词 → 分镜 → 三轨提示词 → 成片包。

与漫剧工坊共享「只产提示词」哲学与锚段一致性纪律,但内容源不同:
漫剧消费小说资产(章节/圣经),宣传片消费「主题 + 角度 + 素材点」——
素材点是事实红线:解说词只允许引用素材点内的事实/数据,不得编造(史实/数据错误即事故)。

流程状态:draft(建了企划)→ chatting 可随时研讨 → briefed(简报已收敛,可再收敛)
→ scripted(解说词)→ storyboarded(分镜)→ ready(提示词齐)。
研讨(chat_log)是多轮流式对话;简报(brief)是研讨结论的结构化契约,锁定后才好进入生成,
但允许改了简报直接重跑下游(各步独立可重跑,同漫剧管线)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class PromoPlan(Base, TimestampMixin):
    """一条宣传片企划(独立于小说项目,按用户隔离)。"""

    __tablename__ = "promo_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # 主题:城市/景区/品牌名(如「西安」)
    subject: Mapped[str] = mapped_column(String(120), default="")
    # 企划名(如「西安·烟火食事」,研讨中可改)
    title: Mapped[str] = mapped_column(String(200), default="")
    # 选中的切入角度(keys,见 engines/promo/common.PROMO_ANGLES)
    angles: Mapped[list[Any]] = mapped_column(JSON, default=list)
    duration_s: Mapped[int] = mapped_column(Integer, default=90)
    # 画风方向(三线共用目录 media/directions.py;宣传片以空镜为主,默认实拍电影感 live)
    direction: Mapped[str] = mapped_column(String(40), default="live")
    # ===== 风格卡(内嵌一条,同漫剧画风锚语义)=====
    style_name: Mapped[str] = mapped_column(String(60), default="")
    style_cn: Mapped[str] = mapped_column(Text, default="")
    style_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    # 地标卡(场景锚):[{name, appearance_cn, appearance_en}]
    landmarks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 素材点:用户给的事实/数据/slogan——解说词的硬约束来源
    material_notes: Mapped[str] = mapped_column(Text, default="")
    # 研讨对话记录:[{role: "user"|"assistant", text}]
    chat_log: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 创作简报(研讨结论的结构化契约,见 engines/promo/brief.py)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    brief_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # 解说词脚本:{synopsis, lines:[{speaker, text, action}]}
    script: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 成片包:{dubbing, narration_full, checklist, totals}(结构同漫剧成片包)
    pack: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 生成切段(按画布拼接工作流):{chunk_s, items:[{index,start_s,end_s,duration_s,
    # shot_seqs,scenes,subtitle,motion_prompt_cn,motion_prompt_en,first_frame_hint,link_note}]}
    # 镜头边界贪心聚段,每段 ≤ chunk_s 秒——一段一次文生视频/图生视频,画布上按段拼接
    chunks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 整片提示词:端到端音频原生视频模型(Sora/Veo/可灵)用的一次性成片提示词,
    # 由分镜+解说词+地标卡+风格卡组装生成;可手改、可整段粘贴保存。
    film_prompt: Mapped[str] = mapped_column(Text, default="")


class PromoShot(Base, TimestampMixin):
    """宣传片分镜:结构同漫剧分镜(三轨提示词),场景关联地标卡(按名匹配)。"""

    __tablename__ = "promo_shots"

    id: Mapped[int] = mapped_column(primary_key=True)
    promo_id: Mapped[int] = mapped_column(
        ForeignKey("promo_plans.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    # 地标/场景名(与 landmarks 卡按名匹配注入场景锚)
    scene_name: Mapped[str] = mapped_column(String(200), default="")
    # 出镜人物(宣传片多为空镜,可空)
    characters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    action_desc: Mapped[str] = mapped_column(Text, default="")
    shot_type: Mapped[str] = mapped_column(String(20), default="")
    camera: Mapped[str] = mapped_column(String(20), default="")
    # 该镜头承载的解说词句(与 script lines 对齐,可空)
    dialogue: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[int] = mapped_column(Integer, default=4)
    # ===== 三轨提示词 =====
    prompt_cn: Mapped[str] = mapped_column(Text, default="")
    prompt_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
