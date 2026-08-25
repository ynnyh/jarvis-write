# app/db/models/birthday.py
"""生日祝福工坊:30/60 秒寿星定制祝福视频,一次产三个本子三选一。

与情绪短片同形(批产→三选一→手卡→出片盘),但输入是结构化「寿星资料」——
定制视频的灵魂在于拍的是**这个人**:称呼/关系/里程碑/回忆点/送出人。
回忆点是红线素材:分镜必须落在给定回忆点上,引擎做关键词核对,
对不上的回忆进 cautions 提示用户核对(定制片最怕张冠李戴的「假回忆」)。

occasion 是扩展位:默认 birthday,后续纪念日/告白/毕业可共用这条线,
目录与提示词再按 occasion 分流(先例见 mood_clips.mode 的 mood/play 双工坊)。

candidates 存三个候选本子(每个自含 logline/情绪曲线/台词/分镜含三轨提示词/金句/切段);
chosen 指向选中序号(-1 未选),clip 存选中的那本(最终态)。分镜内嵌 JSON,不另建表。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class BirthdayWish(Base, TimestampMixin):
    """一条生日祝福视频企划(候选三选一)。按用户隔离;独立于小说项目。"""

    __tablename__ = "birthday_wishes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # 场景扩展位:当前只有 birthday;纪念日/告白/毕业上线时按此分流目录与提示词
    occasion: Mapped[str] = mapped_column(String(20), default="birthday", server_default="birthday")
    # ===== 寿星资料(定制感的全部来源)=====
    # 称呼:小名/外号/关系称呼(如「老王/糖糖/妈」),必须出现在祝福台词里
    honoree_name: Mapped[str] = mapped_column(String(60), default="")
    # 关系 key(见 engines/birthday/common 目录);决定视角与口吻
    relationship: Mapped[str] = mapped_column(String(20), default="")
    # 里程碑自由文本(周岁/18 成人礼/30 而立/60 大寿…),可空=不当里程碑拍
    milestone: Mapped[str] = mapped_column(String(80), default="")
    # 回忆点 2-5 条:每条一个具体场景/梗/口头禅;分镜必须落在这些点上(引擎核对)
    memories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 送出人描述(自己/全家/闺蜜团/部门…),决定「我」还是「我们」的口吻
    sender_desc: Mapped[str] = mapped_column(String(80), default="")
    # ===== 创作参数(对应 clips 的主题/时长/画风)=====
    # 基调 key(见 engines/birthday/common 的 BIRTHDAY_TONES)或空=自定义
    tone: Mapped[str] = mapped_column(String(40), default="")
    custom_tone: Mapped[str] = mapped_column(String(120), default="")
    # 30 / 60 秒(生日片常比情绪短片长:现场投屏/群发都有头有尾)
    duration_s: Mapped[int] = mapped_column(Integer, default=30)
    # 风格包 key(见 engines/birthday/common 的 BIRTHDAY_PACKS,佩奇式/奥特曼式等
    # 角色世界包);空=不用包,走通用画风方向。选包后画风与世界以包为准。
    pack: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # 画风方向(全站共用目录:engines/media/directions.py;pack 为空时生效)
    direction: Mapped[str] = mapped_column(String(40), default="live")
    # 氛围关键词自由文本(≤80 字,注入风格卡),可空
    style_hints: Mapped[str] = mapped_column(String(160), default="", server_default="")
    # ===== 风格卡(批产时一并生成,三个候选共用)=====
    style_name: Mapped[str] = mapped_column(String(60), default="")
    style_cn: Mapped[str] = mapped_column(Text, default="")
    style_en: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    # 三个候选本子:[{take, logline, emotion_curve, lines, shots, punchline, chunks, cautions}]
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 选中序号(-1 未选);选中后 clip 为最终态
    chosen: Mapped[int] = mapped_column(Integer, default=-1)
    clip: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # draft(建了)→ generated(候选已出)→ picked(已选定)
    status: Mapped[str] = mapped_column(String(20), default="draft")


class BirthdayShoot(Base, TimestampMixin):
    """一条祝福片选定本子的「出片工作台」:按段号记生成状态/参考图/成片在哪。

    与 MoodClip 的 ClipShoot 同一取舍:出片状态频繁按段写,独立成表不跟手卡 JSON
    抢读写;成片刻意只记链接/备注不收文件(视频几十 MB,一条片吃满上传配额)。
    祝福片的参考图更多是**寿星真实照片**(回忆杀段图生视频的锚),仍走段级上传。

    shoot 字段是「按段 index → 出片状态」的数组,段号与 BirthdayWish.clip.chunks
    的 index 对齐(手卡改完重算切段后,前端按 index 归并):
    [{index, ref_images:[{kind:'upload'|'url', src, note}], done, result_link, note}]
    """

    __tablename__ = "birthday_shoots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    wish_id: Mapped[int] = mapped_column(
        ForeignKey("birthday_wishes.id", ondelete="CASCADE"), index=True, unique=True
    )
    shoot: Mapped[list[Any]] = mapped_column(JSON, default=list)
