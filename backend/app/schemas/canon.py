# app/schemas/canon.py
# -*- coding: utf-8 -*-
"""故事宪法(Canon):书级恒真「声明」,治长程一致性里「窄窗机制够不着的恒真事实」。

病根(见长程一致性重构方案):当前系统用「逐章演化的圣经」+「相邻章接缝契约」这套
**窄窗**机制,去承载本该「全书恒真」的东西——闭集留白、常驻装置、倒计时期限。这些
书级恒真事实在前几章立下后很快滑出注入窗口(圣经只对本章 characters_involved 注入,
门禁只对照上一章),到第 8 章既进不了生成上下文也进不了门禁比对,于是必漏:
  - 大院「只有三人 / 空荡荡」→ 第 8 章凭空冒出「每天伺候起居」的仆役(缺「刻意留白」)
  - 女主「有系统」→ 好多章系统消失(缺「常驻装置=复现义务」)
  - 「任务倒计时 31 天」→ 各章时间算不清(缺「结构化倒计时」)

Canon 把这些提为一等公民:全程注入 + 全程门禁比对。三类结构化声明——

- absences  刻意留白:那些「没有」是硬设定(大院无仆役 / 女主没有家人 / 此地不通电)。
            闭集约束的正向补充,严禁凭空添人/物来填补。
- devices   常驻装置/金手指:设定为长期存在、需持续复现的东西(系统 / 读心术 / 信物)。
            与伏笔(一次性 plant→payoff)不同,装置是「反复出现的义务」。
- deadline  倒计时:带总天数与起算章的期限(31 天任务),给时间线一个权威锚。
            (Phase 1 只作恒真声明注入;结构化天数算术在 Phase 2 故事时钟落地。)

落库在 projects.canon(JSON,可空),仿 dna/concept 的列范式,罕写、无需 join。
边界纪律(防双真相源):world_rules(自由文本作者规则)保留不动,canon 存结构化声明,
二者在 engines/common.constitution_block 合并渲染成同一个「宪法块」,对用户是一处宪法。

LLM 只提议(canon_suggestions,走建议通道人工确认),绝不自动落库——留白/缺席类
事实靠「检测某物不存在」本就不可靠。见 app/schemas/canon.coerce_canon。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 重要度枚举(与圣经/门禁一致):critical > major > minor
_IMPORTANCE = {"critical", "major", "minor"}


def _coerce_importance(raw: object, default: str = "major") -> str:
    val = str(raw or "").strip().lower()
    return val if val in _IMPORTANCE else default


def _coerce_pos_int(raw: object, default: int = 0) -> int:
    """把 LLM 可能给的 "31"/31/31.0/脏值收敛成非负 int;不可解析回落 default。"""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


class CanonDevice(BaseModel):
    """常驻装置/金手指:设定为长期存在、需持续复现的东西。"""

    name: str = Field(default="", description="装置名,如:系统 / 读心术 / 那枚玉佩")
    cadence: str = Field(
        default="", description="复现节奏,如:每章都应有存在感 / 关键抉择处必出现"
    )
    importance: str = Field(default="major", description="critical|major|minor")


class CanonDeadline(BaseModel):
    """倒计时:带权威总天数与起算章的全书期限。"""

    name: str = Field(default="", description="倒计时名,如:任务倒计时 / 大婚之期")
    total_days: int = Field(default=0, description="总天数,如 31")
    anchor_chapter: int = Field(default=1, description="从第几章起算")
    importance: str = Field(default="critical", description="critical|major|minor")


class StoryCanon(BaseModel):
    """一部小说的「故事宪法」:书级恒真声明。全部字段可空。"""

    absences: list[str] = Field(
        default_factory=list, description="刻意留白:那些『没有』是硬设定"
    )
    devices: list[CanonDevice] = Field(
        default_factory=list, description="常驻装置/金手指清单"
    )
    deadline: CanonDeadline | None = Field(default=None, description="全书倒计时,可空")

    def is_empty(self) -> bool:
        """三类声明全空才算无宪法(等价于没设)。"""
        return not (
            any((a or "").strip() for a in self.absences)
            or any(d.name.strip() for d in self.devices)
            or (self.deadline is not None and self.deadline.name.strip())
        )

    def render(self) -> str:
        """渲染成「宪法(结构化)」正文,供 constitution_block 拼接注入生成与门禁。

        只输出非空部分;全空返回空串(调用方据此决定是否出整块)。
        措辞直接写成「可执行硬约束」,让生成端照做、门禁端可对照。
        """
        blocks: list[str] = []

        absences = [a.strip() for a in self.absences if (a or "").strip()]
        if absences:
            blocks.append(
                "刻意留白(以下这些『没有』是本书硬设定,绝不能凭空添加人/物/设定来填补;"
                "尤其不得让从未登场的常驻角色「一直都在」):\n"
                + "\n".join(f"  - {a}" for a in absences)
            )

        devices = [d for d in self.devices if d.name.strip()]
        if devices:
            dev_lines = []
            for d in devices:
                cad = f",复现节奏:{d.cadence.strip()}" if d.cadence.strip() else ""
                dev_lines.append(
                    f"  - {d.name.strip()}(常驻,贯穿全书{cad}):"
                    "已确立即长期有效,不可无故长期消失、被遗忘或与其设定矛盾"
                )
            blocks.append(
                "常驻装置/金手指(设定为长期存在,须按其节奏持续复现,不是一次性道具):\n"
                + "\n".join(dev_lines)
            )

        dl = self.deadline
        if dl is not None and dl.name.strip():
            days = f",共 {dl.total_days} 天" if dl.total_days > 0 else ""
            blocks.append(
                f"倒计时:{dl.name.strip()}{days}(自第 {dl.anchor_chapter} 章起算);"
                "各章提到的时间流逝与剩余天数必须与此一致——不得算乱、前后矛盾或无限拖延。"
            )

        return "\n\n".join(blocks)


def coerce_canon(raw: object) -> StoryCanon:
    """把任意来源(LLM dict / 存量 None / 脏数据)收敛成合法 StoryCanon。

    - None / 非 dict → 空宪法
    - dict → 只取已知字段;absences 规整成非空 str 列表;devices 丢掉无名条目;
      deadline 需有 name 才成立,天数/起算章脏值回落默认。未知键一律丢弃。
    """
    if not isinstance(raw, dict):
        return StoryCanon()

    absences_raw = raw.get("absences")
    if isinstance(absences_raw, str):
        absences_raw = [absences_raw]
    absences = (
        [str(a).strip() for a in absences_raw if str(a).strip()]
        if isinstance(absences_raw, list)
        else []
    )

    devices: list[CanonDevice] = []
    for d in raw.get("devices") or []:
        if isinstance(d, dict) and str(d.get("name") or "").strip():
            devices.append(
                CanonDevice(
                    name=str(d["name"]).strip(),
                    cadence=str(d.get("cadence") or "").strip(),
                    importance=_coerce_importance(d.get("importance")),
                )
            )

    deadline: CanonDeadline | None = None
    d = raw.get("deadline")
    if isinstance(d, dict) and str(d.get("name") or "").strip():
        deadline = CanonDeadline(
            name=str(d["name"]).strip(),
            total_days=_coerce_pos_int(d.get("total_days")),
            anchor_chapter=_coerce_pos_int(d.get("anchor_chapter"), default=1) or 1,
            importance=_coerce_importance(d.get("importance"), default="critical"),
        )

    return StoryCanon(absences=absences, devices=devices, deadline=deadline)
