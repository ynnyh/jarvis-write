# app/engines/tendency/assembler.py
# -*- coding: utf-8 -*-
"""倾向拼装器:把用户选的标签转成可注入 Prompt 的指令文本。

拼装规则(docs/04-tag-system.md §4.2):
1. 遍历用户选中的维度;
2. 单选维度取对应片段,多选维度拼接多个片段;
3. 自定义值(_custom)作为用户指令原样注入,前缀「用户额外要求:」;
4. 汇总为「本次写作倾向」文本块,插入 Prompt 的 {style_directives} 占位符。

两层作用域(§5):最终倾向 = 全局基调 被 单次临时值覆盖。
"""
from __future__ import annotations

from typing import Any

from app.schemas.tendency import AssembledTendency, Tendency

from .catalog import _dimension_select, _directive_index

_CUSTOM_KEY = "_custom"
# 创作偏好档案:贯穿全书的创作宪法,存在 global_tendency[_PROFILE_KEY] 子字典里。
# 与 _custom(按维度零散自定义)不同,它是结构化的整书主张,注入所有生成环节,
# 优先级高于「本次写作倾向」。字段 -> 注入 prompt 时的中文标签(顺序即展示顺序)。
_PROFILE_KEY = "_profile"
_PROFILE_LABELS = [
    ("style", "文风"),
    ("taboos", "禁忌/避雷"),
    ("audience", "读者定位"),
    ("other", "其他创作主张"),
]


def merge_tendency(
    global_tendency: Tendency | None,
    override: Tendency | None,
) -> Tendency:
    """合并全局与单次临时倾向:单次未指定的维度回落到全局。

    _custom 子字典单独按 key 合并,同样是 override 优先。
    """
    merged: Tendency = dict(global_tendency or {})
    override = override or {}

    for key, value in override.items():
        if key in (_CUSTOM_KEY, _PROFILE_KEY):
            continue
        merged[key] = value

    # _custom 单独合并
    g_custom = dict((global_tendency or {}).get(_CUSTOM_KEY) or {})
    o_custom = dict(override.get(_CUSTOM_KEY) or {})
    g_custom.update(o_custom)
    if g_custom:
        merged[_CUSTOM_KEY] = g_custom

    return merged


def assemble_tendency(
    node: str,
    tendency: Tendency | None,
    global_tendency: Tendency | None = None,
) -> AssembledTendency:
    """把倾向拼装成『本次写作倾向』文本块。

    node: outline / chapter / polish —— 决定查哪张指令片段映射表。
    未选任何倾向时返回空文本(Prompt 里该块整体省略)。
    未知维度或未知 chip 文案:当作自定义值处理,不丢弃(用户手输的
    文案本身就是语义,直接给模型)。
    """
    merged = merge_tendency(global_tendency, tendency)
    index = _directive_index(node)
    select_map = _dimension_select(node)

    lines: list[str] = []
    applied: dict[str, Any] = {}

    for key, value in merged.items():
        if key in (_CUSTOM_KEY, _PROFILE_KEY) or value in (None, "", []):
            continue

        # 归一成列表处理;单选维度只取第一个
        values = value if isinstance(value, list) else [value]
        if select_map.get(key) == "single":
            values = values[:1]

        dim_index = index.get(key, {})
        for label in values:
            directive = dim_index.get(str(label))
            if directive:
                lines.append(f"- {directive}")
            else:
                # 不在预设里 → 视为用户自定义语义,原样注入
                lines.append(f"- 用户额外要求:{label}")
        applied[key] = value if isinstance(value, list) else values[0]

    # _custom:显式的「我要输入」内容
    for key, custom_value in (merged.get(_CUSTOM_KEY) or {}).items():
        if custom_value:
            lines.append(f"- 用户额外要求({key}):{custom_value}")
            applied.setdefault(_CUSTOM_KEY, {})
            applied[_CUSTOM_KEY][key] = custom_value

    # 创作偏好档案:结构化的整书主张,单独成块(优先级高于本次写作倾向)
    profile = merged.get(_PROFILE_KEY) or {}
    profile_lines: list[str] = []
    if isinstance(profile, dict):
        for key, label in _PROFILE_LABELS:
            val = str(profile.get(key) or "").strip()
            if val:
                profile_lines.append(f"- {label}:{val}")
    profile_text = "\n".join(profile_lines)

    directives_text = "\n".join(lines)
    return AssembledTendency(
        directives_text=directives_text, applied=applied, profile_text=profile_text
    )


def render_style_block(assembled: AssembledTendency) -> str:
    """渲染成注入 Prompt 的整块文本;档案与倾向皆无时返回空串。

    创作偏好档案是贯穿全书的最高优先级约束,排在「本次写作倾向」之前。
    """
    blocks: list[str] = []
    if assembled.profile_text:
        blocks.append(
            f"【创作偏好档案(贯穿全书的最高优先级约束,务必遵守)】\n{assembled.profile_text}\n"
        )
    if assembled.directives_text:
        blocks.append(f"【本次写作倾向】\n{assembled.directives_text}\n")
    return "\n".join(blocks)
