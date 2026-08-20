# app/schemas/dna.py
# -*- coding: utf-8 -*-
"""故事 DNA(创作坐标 / 本书基因):概念之上的「定味道」锚。

病根:选了「青春校园」却生成「学生觉醒能力」——题材标签只说了「分类」,
没说「要哪种味道」;而唯一那条题材约束还待在最低优先级的位置(见
engines/tendency/assembler.render_style_block 的「本次写作倾向」块)。
StoryDNA 把用户真正想要的「味道」捕获成一个可确认、可注入、可检测的锚,
贯穿 概念→脊柱→正文 全链路。

它是正文层「文风范本(voice_key/voice_sample)+ 配对反例」在方向层的对应物:
- comps / vibe   参照系(像《X》/ 自备 vibe 范本),对应 voice 的「学这种」
- must / must_not 该题材的「必须有 / 绝不能有」(硬门素材,喂给禁忌门)
- axes           味道轴(节奏 / 基调 / 笔触 / 结局 / 尺度),给用户与检测器一致的抓手
- capsule        蒸馏出的「本书基因」整块文本(品味镜展示 + 强位注入)

落库在 projects.dna(JSON,可空)。字段全部可空:坐标是逐步捏出来的。
版权红线(沿用 prompts/style_capsules 的纪律):vibe / capsule 只描述「味道」,
绝不嵌入在世作家原作节选;comps 只作指路参照名,不搬其内容。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 题材模式:硬开关,驱动禁忌门(见 engines/drift)。"" = 未定(不启用硬门)。
DNA_MODES: tuple[tuple[str, str], ...] = (
    ("realistic", "现实向"),
    ("fantasy", "幻想向"),
    ("mixed", "混合向"),
)
_MODE_KEYS = {k for k, _ in DNA_MODES}
_MODE_LABEL = {k: label for k, label in DNA_MODES}

# 味道轴:给用户滑杆、给检测器抓手。(key, 标签, 左端, 右端)。
# 值存该轴的位置标签(左端 / 右端 / 或居中的自定义词);空 = 该轴不表态。
TASTE_AXES: tuple[tuple[str, str, str, str], ...] = (
    ("pace", "节奏", "快", "慢"),
    ("sweetness", "基调", "甜", "虐"),
    ("realism", "笔触", "写实", "梦幻"),
    ("ending", "结局", "圆满", "遗憾"),
    ("drama", "尺度", "日常", "戏剧化"),
)
_AXIS_KEYS = {k for k, *_ in TASTE_AXES}
_AXIS_LABEL = {k: label for k, label, *_ in TASTE_AXES}


class StoryDNA(BaseModel):
    """一部小说的「味道锚」。全部字段可空:坐标是逐步捏出来的。"""

    comps: str = Field(default="", description="参照系:像《X》/《X》遇上《Y》")
    mode: str = Field(default="", description="题材模式:realistic/fantasy/mixed")
    axes: dict[str, str] = Field(
        default_factory=dict, description="味道轴:轴 key → 位置标签"
    )
    must: list[str] = Field(default_factory=list, description="必须有的元素/看点")
    must_not: list[str] = Field(
        default_factory=list, description="绝不能有的元素(禁忌,喂给禁忌门)"
    )
    vibe: str = Field(
        default="", description="用户自备的 vibe 范本(只描述味道,非原作节选)"
    )
    taste_key: str = Field(
        default="", description="选中的精选味道锚 key(见 prompts/dna_capsules)"
    )
    capsule: str = Field(default="", description="蒸馏出的『本书基因』整块文本")

    def is_empty(self) -> bool:
        """所有维度都没表态才算无 DNA。"""
        return not any(
            [
                self.comps.strip(),
                self.mode.strip(),
                self.vibe.strip(),
                self.taste_key.strip(),
                self.capsule.strip(),
                any((v or "").strip() for v in self.axes.values()),
                any((x or "").strip() for x in self.must),
                any((x or "").strip() for x in self.must_not),
            ]
        )

    def mode_label(self) -> str:
        """题材模式的中文标签;未定/未知返回空串。"""
        return _MODE_LABEL.get(self.mode, "")

    def axes_text(self) -> str:
        """味道轴渲染成一行『标签:位置』,只输出表了态的轴,顺序稳定。"""
        parts = []
        for key, label, *_ in TASTE_AXES:
            val = (self.axes.get(key) or "").strip()
            if val:
                parts.append(f"{label}:{val}")
        return " · ".join(parts)

    def render(self) -> str:
        """渲染成『本书基因』块:供品味镜展示与强位注入。只输出非空部分。

        结构化字段(参照/味道/必须有/绝不能有)始终显式成行——它们是可
        检测、可执行的硬约束;capsule 作为整体基调补在后面;vibe 范本截断防爆窗。
        """
        lines: list[str] = []
        if self.comps.strip():
            lines.append(f"参照坐标:{self.comps.strip()}")
        mode_label = self.mode_label()
        if mode_label:
            lines.append(f"题材模式:{mode_label}")
        axes = self.axes_text()
        if axes:
            lines.append(f"味道轴:{axes}")
        must = [m.strip() for m in self.must if m and m.strip()]
        if must:
            lines.append(f"必须有:{'、'.join(must)}")
        must_not = [m.strip() for m in self.must_not if m and m.strip()]
        if must_not:
            tag = "(现实向硬门)" if self.mode == "realistic" else ""
            lines.append(f"绝不能有{tag}:{'、'.join(must_not)}")
        if self.capsule.strip():
            lines.append(f"整体基调:{self.capsule.strip()}")
        if self.vibe.strip():
            lines.append(
                "作者自备 vibe 参照(只学味道,不搬内容):\n"
                + self.vibe.strip()[:800]
            )
        return "\n".join(lines)


def coerce_dna(raw: object) -> StoryDNA:
    """把任意来源(LLM dict / 存量 None / 脏数据)收敛成合法 StoryDNA。

    - None / 非 dict → 空 DNA
    - dict → 只取已知字段;str 字段转 str 并 strip;list 字段规整成非空 str 列表;
      axes 只留已知轴键且值非空;mode 只认已知模式,否则清空。未知键一律丢弃。
    """
    if not isinstance(raw, dict):
        return StoryDNA()

    def _s(key: str) -> str:
        return str(raw.get(key) or "").strip()

    def _list(key: str) -> list[str]:
        val = raw.get(key)
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            return []
        return [str(x).strip() for x in val if str(x).strip()]

    axes: dict[str, str] = {}
    axes_raw = raw.get("axes")
    if isinstance(axes_raw, dict):
        for key, val in axes_raw.items():
            if key in _AXIS_KEYS and str(val or "").strip():
                axes[key] = str(val).strip()

    mode = _s("mode")
    if mode not in _MODE_KEYS:
        mode = ""

    return StoryDNA(
        comps=_s("comps"),
        mode=mode,
        axes=axes,
        must=_list("must"),
        must_not=_list("must_not"),
        vibe=_s("vibe"),
        taste_key=_s("taste_key"),
        capsule=_s("capsule"),
    )
