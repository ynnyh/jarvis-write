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

import json
import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Outline, Project

logger = logging.getLogger("jarvis-write.common")


# ---- LLM JSON 输出解析(从 consistency/extractor 下沉至此:主审/契约/抽取/检查共用) ----

def parse_llm_json(text: str) -> dict:
    """宽容解析 LLM 输出的 JSON:剥 markdown 围栏、截取首尾大括号。

    兼容性入口:解析失败返回空 dict。**关键链路上不要用这个函数**——它无法区分
    「模型说没问题」与「模型的话没解析出来」,后者会被下游当成干净结果(静默降级)。
    关键链路请改用 parse_llm_json_checked,拿得到失败原因。
    """
    data, _err = parse_llm_json_checked(text)
    return data


def parse_llm_json_checked(text: str) -> tuple[dict, str | None]:
    """宽容解析 + 显式成败:返回 (数据, 失败原因);成功时原因为 None。

    与 parse_llm_json 的区别只在「失败看得见」:调用方能据此走显式降级
    (标 degraded 待人工复核),而不是把没解析出来的输出当成「没有问题」。
    """
    raw = (text or "").strip()
    if not raw:
        return {}, "模型返回空内容"
    s = raw
    # 剥 ```json ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    # 截取最外层大括号
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start : end + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        logger.warning("LLM JSON 解析失败: %s;原文前200字: %s", exc, raw[:200])
        return {}, f"JSON 解析失败({exc.msg} @ 位置 {exc.pos})"
    if not isinstance(data, dict):
        return {}, f"顶层不是对象(得到 {type(data).__name__})"
    return data, None


_CONTINUE_TAIL = 800
# 续写指令:不重发原 prompt——原 prompt 往往上万字,而断点之后该写什么,
# 前缀里的结构已经说清楚了(LLM 最擅长的就是模式补全)。输入短 + 输出短,
# 正好绕开「输出越长越容易被网关掐断」这个死循环。
_CONTINUE_INSTRUCTION = (
    "下面是一段 JSON 输出,它在生成到一半时被网络中断,停在半途。"
    "请只输出它**缺失的后半部分**,与你看到的内容首尾相接拼成完整 JSON。\n"
    "要求:直接接着写,不要重复已给出的内容,不要加任何解释或 markdown 围栏。\n\n"
    "被中断的输出:\n"
)


def _strip_fence(text: str) -> str:
    """剥掉 markdown 围栏(续写片段可能整段被模型包进 ```json)。"""
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    return (m.group(1).strip() if m else s)


def _can_continue(raw: str) -> bool:
    """半截输出值得续写吗(空/纯废话就直接整篇重发,别浪费一轮)。"""
    s = _strip_fence(raw)
    return "{" in s and len(s) >= 8


def _continuation_prompt(raw: str) -> str:
    tail = _strip_fence(raw)
    if len(tail) > _CONTINUE_TAIL:
        tail = tail[-_CONTINUE_TAIL:]
    return _CONTINUE_INSTRUCTION + tail


def _stitch(raw: str, cont: str) -> str:
    """把续写片段接到半截输出后面(两者都先剥围栏)。"""
    return _strip_fence(raw) + _strip_fence(cont)


async def ask_llm_json(
    adapter, prompt: str, *, label: str = "JSON 任务", attempts: int = 2,
    continue_attempts: int = 2,
) -> tuple[dict, str | None]:
    """LLM JSON 任务统一调用 + 两级保险:先「续写补完」,再「整篇重发」。

    背景(2026-09-08 50 章压测):中转渠道会把响应尾巴随机截断——连
    `{"issues": []`(16 字符)都被截在半途,官方渠道同构调用零失败。

    为什么续写优先于整篇重发:输出越长越容易被掐。原样重发一次,输出长度
    一字不差,等于再撞一次同样的概率——实测 9 章隔离章重跑只救回 2 章,
    还白烧 87.8 万 token。改成把已收到的半截前缀回传、让模型只补剩余部分,
    单次输出大幅变短,既省钱又真正提高成功率。救不回来才整篇重发。

    LLM 调用本身的异常不吞,原样上抛——调用方各自有「调用失败」的降级分支,
    与「解析失败」是两种不同的现场。但**续写调用**的异常要吞:它只是抢救
    动作,不该让一次本来能靠重发救回的任务就此失败。
    """
    data, err = {}, "模型返回空内容"
    for attempt in range(1, attempts + 1):
        raw = await adapter.ask(prompt)
        data, err = parse_llm_json_checked(raw)
        if not err:
            if attempt > 1:
                logger.warning("%s:第 %d 次解析成功(首次疑似响应被截断)", label, attempt)
            return data, None
        for cont_no in range(1, continue_attempts + 1):
            if not _can_continue(raw):
                break
            try:
                cont = await adapter.ask(_continuation_prompt(raw))
            except Exception as exc:  # noqa: BLE001 — 抢救动作失败不致命
                logger.warning("%s:续写第 %d 次调用失败(%s),改走整篇重发", label, cont_no, exc)
                break
            stitched = _stitch(raw, cont)
            data, err = parse_llm_json_checked(stitched)
            if not err:
                logger.warning("%s:第 %d 次尝试续写第 %d 轮后拼成完整 JSON", label, attempt, cont_no)
                return data, None
            raw = stitched
        logger.warning("%s:第 %d/%d 次输出解析失败:%s", label, attempt, attempts, err)
    return {}, err


# ---- 降级哨兵(degrade sentinel)----
# 任一条带 degraded=True 的记录都表示「这一环节没跑成」,不是「查过没问题」。
DEGRADED_KEY = "degraded"

SCOPE_CONSISTENCY = "一致性检查"
SCOPE_FACT_EXTRACT = "章后事实抽取"
SCOPE_REVIEW = "主审评分"
SCOPE_SCENE_PLAN = "场景切分"
SCOPE_SCENE_ACCEPT = "场景验收"

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
