# app/db/models/scene.py
"""场景卡:把「章」降级为容器,把「场景」升格为真正的生成单元。

为什么要有这张表(而不是往 outlines 塞一个 JSON 列):
  一次 LLM 调用写一整章,模型要同时兼顾 31 个注入变量,只能给出最保守的平均解
  ——这正是「每章都工整、都挑不出毛病,但都像喝白水」的结构性根因。要让
  「该精彩处放开、该压抑处压住」落地,必须让每个场景成为独立的生成与验收单元:
  每个场景只带它需要的事实、只收到一个明确的情绪指令、只被验收一次。

  场景一旦成为生成单元,就必须有自己的状态机(待生成/已生成/已验收/已废弃)、
  版本号、指向正文的锚点、验收记录。塞进 JSON 列里,第二个功能就会开始拆字符串。

字段分工:
  - 蓝图侧(planning):title / summary / location / characters / goal / conflict
    / emotion_target / tension_level / target_words —— 蓝图阶段切好,生成时只「填肉」
  - 正文侧(realized):status / content / word_count / anchor_start / anchor_end
    —— 逐场景生成后回填,锚点是场景在章正文里的字符区间
  - 验收侧(verdict):accept_note / accept_scores / rewrite_count
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin

# 场景状态机:planned 蓝图已切好 / drafting 正在写 / drafted 已生成 / accepted
# 已验收通过 / rejected 验收未过待重写 / discarded 用户或重规划废弃
SCENE_STATUSES = (
    "planned",
    "drafting",
    "drafted",
    "accepted",
    "rejected",
    "discarded",
)


class Scene(Base, TimestampMixin):
    """一个场景 = 一次生成 + 一次验收的最小单元。

    一章 3-5 个场景;场景按 seq 在章内排序;anchor_start/anchor_end 记录该场景
    正文在 Chapter.final_content 里的字符区间(场景级验收/定点重写靠它定位)。
    """

    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # 关联蓝图:场景是蓝图的一部分,蓝图改了场景可以重切
    outline_id: Mapped[int] = mapped_column(
        ForeignKey("outlines.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    # 章内序号,从 1 起
    seq: Mapped[int] = mapped_column(Integer, default=1)

    # ---- 蓝图侧(切分时定下)----
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    characters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 这一场要推进什么(场景目标):读者读完这一场,知道了什么/谁变了
    goal: Mapped[str] = mapped_column(Text, default="")
    # 这一场的冲突或悬念:没有冲突的场景就是白水
    conflict: Mapped[str] = mapped_column(Text, default="")
    # 情绪指令:这一场要让读者感受到什么(与 outline.emotional_tone 是「章」与
    # 「场」的关系——章定调,场定强弱与转折)
    emotion_target: Mapped[str] = mapped_column(String(100), default="")
    # 张力强度 1-5:章内张力曲线的落点。1=压抑/铺垫,3=常规推进,5=总爆发。
    # 生成时按它下发「放开写 / 压住写」的力度指令,这是治白水的直接开关。
    tension_level: Mapped[int] = mapped_column(Integer, default=3)
    target_words: Mapped[int] = mapped_column(Integer, default=1500)
    # 本场需要携带的事实提示(检索层的种子 query;留空则由 title+summary 检索)
    fact_hints: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # ---- 正文侧(生成后回填)----
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    # 该场景正文在 Chapter.final_content 中的 [start, end) 字符区间;
    # 由 char offset 精确切章得到,不用字符串搜索(避免重复段落定位歧义)
    anchor_start: Mapped[int] = mapped_column(Integer, default=0)
    anchor_end: Mapped[int] = mapped_column(Integer, default=0)
    # 场景是否与上一场直接衔接(不空行分隔);章内首场恒为 False
    joins_previous: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- 验收侧 ----
    # 末次验收结论(给用户看的一句话)
    accept_note: Mapped[str] = mapped_column(Text, default="")
    # 末次验收的结构化结果(四维/情绪到位度/字数比…)
    accept_scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 重写次数(封顶 2 次后回退重规划,见 D4)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SceneVersion(Base):
    """场景版本快照:场景级重写的回退与 diff 验收依赖它。

    与 OutlineVersion / ChapterVersion 同一套思路——生成单元自己要有历史,
    否则「只重写这一个场景」就没有可对比的旧版,用户也无从判断改好了还是改坏了。
    """

    __tablename__ = "scene_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    # generated / rewritten / accepted / manual(用户手改)
    source: Mapped[str] = mapped_column(String(20), default="generated")
    # 存这次版本的原因(验收未过的维度、用户的修改意见)
    note: Mapped[str] = mapped_column(Text, default="")
