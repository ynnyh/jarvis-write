# app/engines/common.py
# -*- coding: utf-8 -*-
"""引擎层公共小工具:大纲查询、架构简报、以及**显式降级**语义。

architecture_brief 两个变体是按场景定制的提示词素材,字段取舍不同,
保留两份不合并(合并会改变生成行为)。

关于降级(2026-09-08 补):
此前「LLM 调用失败 / JSON 解析失败」一律静默返回空(空列表 / 空 dict),
下游无从区分「确实没查出问题」与「这一环节根本没跑成」——一致性门禁因此在
模型超时/429 时自动放行,「不崩」的承诺在最需要它的时刻失效。
现改为显式降级:失败时返回带 `degraded` 标记的哨兵,由下游显式处理
(隔离待人工复核 / 章末标记),绝不冒充「干净」。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Outline, Project

# ---- 降级哨兵(degrade sentinel)----
# 任一条带 degraded=True 的记录都表示「这一环节没跑成」,不是「查过没问题」。
DEGRADED_KEY = "degraded"

SCOPE_CONSISTENCY = "一致性检查"
SCOPE_FACT_EXTRACT = "章后事实抽取"
SCOPE_REVIEW = "主审评分"

# 降级时连续性维度的取值:0 分是刻意的——它不是「连续性差」,而是「未校验」。
# 绝不能回落成 9(干净),那等于把没跑过的检查当成通过了。
CONTINUITY_UNVERIFIED = 0


def degraded_issue(scope: str, reason: str) -> dict:
    """构造一条降级哨兵问题(形状与 check_chapter 的 issue 一致,可直接落库/回显)。

    severity 取 minor 是算过的:降级不该一票否决(那会让模型一抽风整章就卡死),
    但必须**可见**——落进 chapter_issues 并在章节卡片显示「未经校验」。
    """
    return {
        DEGRADED_KEY: True,
        "scope": scope,
        "reason": reason,
        "severity": "minor",
        "type": "degraded",
        "description": f"【未校验】{scope}未能完成({reason}),本章未经该环节校验,请人工复核",
        "evidence": "",
        "conflicting_fact": "",
        "suggestion": f"模型/网络恢复后重跑{scope}(可在章节问题面板手动触发复查)",
        "fix_mode": "patch",
    }


def degraded_stats(scope: str, reason: str) -> dict:
    """抽取类环节的降级返回(替代原来的空 dict):调用方据此判断成败。"""
    return {DEGRADED_KEY: True, "scope": scope, "reason": reason}


def degraded_of(items: object) -> list[dict]:
    """从列表里挑出降级哨兵。"""
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get(DEGRADED_KEY)]


def is_degraded(items: object) -> bool:
    """列表里是否含降级哨兵(针对 list 型结果,如 check_chapter)。"""
    return bool(degraded_of(items))


def stats_degraded(stats: object) -> bool:
    """抽取/统计型结果是否为降级(dict 型,如 extract_and_apply 的返回值)。"""
    return isinstance(stats, dict) and bool(stats.get(DEGRADED_KEY))


def get_outline(db: Session, project_id: int, n: int) -> Outline | None:
    return (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )


def _concept_spark_block(project: Project) -> str:
    """把灵感工坊的原始火花(钩子/反转/主角/冲突/基调)回注逐章生成。

    信息链路是有损的:concept → 核心种子(≤100字)→ 蓝图简述(≤100字)→ 正文,
    最初的鲜活细节到章节层已被抽象成骨架。这里把 concept 的原始表述再带一份进来,
    让每章都能回看这本书"最打动人的那点东西",别只照着干巴巴的骨架填肉。
    """
    from app.schemas.concept import coerce_concept

    c = coerce_concept(project.concept)
    if c.is_empty():
        return ""
    # 只带最能定调的几项:钩子/反转/主角/冲突/基调(logline 已进核心种子,不重复)
    spark_fields = [
        ("hook", "核心钩子"),
        ("twist", "潜在反转"),
        ("protagonist", "主角"),
        ("conflict", "核心冲突"),
        ("setting", "世界/基调"),
    ]
    lines = [
        f"- {label}:{value.strip()}"
        for key, label in spark_fields
        if (value := getattr(c, key)).strip()
    ]
    if not lines:
        return ""
    return (
        "\n\n本书的创作初衷(始终记住这本书最打动人的地方,别写成套路化的骨架):\n"
        + "\n".join(lines)
    )


def world_rules_block(project: Project) -> str:
    """世界观硬规则(钉板)注入块:用户在项目里钉死的"不可违背的设定/常识"。

    附在架构简报后,注入草稿/定稿/大纲改写/修改指令等所有生成环节——
    高考制度、年代规则、世界观硬设定这类模型容易临场编错的东西,钉一次全书生效。

    注:本函数只渲染自由文本 world_rules(保持向后兼容与既有测试语义);结构化
    故事宪法(canon)由 constitution_block 在其之上合并——两者对用户是「一处宪法」。
    """
    rules = (project.world_rules or "").strip()
    if not rules:
        return ""
    return (
        "\n\n【本书世界观硬规则——绝对不可违背,写到相关设定时必须照此执行,"
        "与你的常识冲突时以本规则为准】:\n" + rules
    )


def constitution_block(project: Project) -> str:
    """本书宪法块(统一):自由文本世界观硬规则 + 结构化故事宪法(canon)。

    治长程一致性里「窄窗机制够不着的书级恒真事实」——闭集留白 / 常驻装置 / 倒计时。
    这些设定在前几章立下后很快滑出圣经与契约的注入窗口,到后段既进不了生成上下文、
    也进不了门禁比对(见 app/schemas/canon.py 病根)。故把它们提为一等公民:经本块
    全程注入生成、经门禁全程比对。

    边界(防双真相源):world_rules 保留不动(仍走 world_rules_block),canon 存
    结构化声明,二者在此合并成同一个「宪法块」。canon 为空时输出与 world_rules_block
    逐字相同(向后兼容,老项目行为不变)。
    """
    from app.schemas.canon import coerce_canon

    wr = world_rules_block(project)
    canon_text = coerce_canon(getattr(project, "canon", None)).render()
    if not canon_text:
        return wr  # 无 canon → 完全等价于旧 world_rules_block(向后兼容)
    return wr + (
        "\n\n【本书故事宪法(结构化)——全书恒真,绝对不可违背;与上下文/常识冲突时以此为准】\n"
        + canon_text
    )


def chapter_architecture_brief(project: Project) -> str:
    """逐章生成用:创作初衷 + 核心种子 + 世界观 + 角色动力学 + 世界观硬规则。

    角色动力学给足篇幅并点明用途——不只是背景设定,更是揣摩各角色说话口吻、
    性格差异的依据(让笔下人物各有各的声音,是长篇质感的关键)。
    concept 的原始火花回注,缓解"灵感→种子→蓝图→正文"链路的信息减损。
    尾部拼「本书宪法块」(world_rules + 结构化 canon),钉死留白/装置/倒计时。
    """
    arch = project.architecture
    if arch is None:
        base = "(无)"
    else:
        base = (
            f"核心种子:{arch.core_seed}\n\n"
            f"世界观(节选):{arch.world_building[:600]}\n\n"
            f"角色动力学(据此揣摩各角色的性格、处境与说话口吻,让他们的声音互不相同):\n"
            f"{arch.character_dynamics[:1800]}"
            f"{_concept_spark_block(project)}"
        )
    return base + constitution_block(project)


def cascade_architecture_brief(project: Project) -> str:
    """级联重生成用:核心种子 + 情节架构。"""
    arch = project.architecture
    if arch is None:
        return "(无)"
    return f"核心种子:{arch.core_seed}\n情节架构(节选):{arch.plot_architecture[:800]}"
