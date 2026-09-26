# backend/scripts/exp_book_plans.py
# -*- coding: utf-8 -*-
"""docs/22 去险实验:「整书方案×3 + 定向修订」prompt 草案的真模型验证。

验证全方案最大的假设(docs/22 §5 风险 1):
1. 具体度——方案卡是否具体到「具体的人/困境/反差」,不空泛(靠人工读输出评);
2. 差异度——同一轮 3 套方案之间的差异轴是否拉开(字符二元组 Jaccard 重叠 + label 唯一性);
3. 方差——同输入两次运行是否都可用(A vs A2);
4. 修订服从度——一句话定向修订是否只改该改的字段、连带一致(字段级 diff + 预期外改动计数);
5. 解析健壮性——JSON 一次通过率。

用法: python -m scripts.exp_book_plans  (本地 dev 库, admin 的 provider 配置)
结果: backend/tmp_exp_book_plans_result.md
"""
from __future__ import annotations

import asyncio
import time

from app.auth import current_user_id
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for

RESULT_PATH = "tmp_exp_book_plans_result.md"

GENRE_STRICT = (
    "严守题材边界:方案只能在灵感碎片描述与所选题材的规则内发挥,"
    "不得擅自引入描述之外的非现实设定(如超能力、异能觉醒、系统、金手指、穿越等),"
    "除非作者明确要求。"
)
GENRE_FREE = "题材未定,方向可自由发挥;但同样不吃老套路(觉醒/系统/重生/穿越,除非作者明确要求)。"

BOOK_PLANS_PROMPT = """\
你是资深故事策划,正在开选题会。基于作者的初始想法,一次给出 3 套完整的「整书方案」。
每套方案的所有格子都由你写满、大胆定细节——作者的参与方式是挑一套再改,不是先答题;
你不许留空、不许写「待定」。

【作者的初始想法/已选方向(可能为空)】
{topic}

要求:
1. 每套方案包含以下字段:
   - title 书名(暂定,起个有钩子的)
   - kernel 故事内核(一句话:什么人+陷进什么局面+靠什么破局)
   - protagonist 主角小传(3 句:身份+想要什么+底色,必须是具体的人)
   - world 世界观底盘(2 句:时代舞台+力量或规则体系)
   - arc 首卷走向(3 句:开局钩子+核心冲突+卷尾悬念)
   - engine 连载引擎(1 句:靠什么长期吊着读者)
   - flavor 味道标签(2-3 个词)
2. 3 套方案差异必须拉开:不同主角类型 × 不同冲突来源 × 不同味道基调,严禁同质化;
   每套用 label「主角类型·冲突来源·味道」标注差异坐标
3. 具体的人、具体的困境、具体的反差,严禁空泛概括
4. {genre_boundary}

严格按 JSON 输出(不要 markdown 围栏,不要任何解释):
{{"plans": [
  {{"title": "", "kernel": "", "protagonist": "", "world": "", "arc": "", "engine": "", "flavor": [""], "label": ""}}
]}}"""

REVISE_PLAN_PROMPT = """\
你是资深故事策划。作者选定了下面这套整书方案,并给了一句修改要求。
只按修改要求修订这套方案:要求涉及的部分认真改,和它强关联的内容连带着改顺
(如主角换了性别,人称与相关设定要一致);其余内容尽量保持原样,不要顺手发挥。
输出仍是一套字段齐全的完整方案。

【当前方案(JSON)】
{plan_json}

【作者的修改要求】
{directive}

严格按 JSON 输出(不要 markdown 围栏,不要任何解释):
{{"title": "", "kernel": "", "protagonist": "", "world": "", "arc": "", "engine": "", "flavor": [""], "label": ""}}"""

FIELDS = ["title", "kernel", "protagonist", "world", "arc", "engine", "flavor", "label"]

PLAN_CASES = [
    ("A-镖师一句话", "落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人", None),
    ("A2-同输入方差", "落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人", None),
    ("B-都市悬疑无线索", "", "都市悬疑"),
    ("C-女主事业", "女主白手起家搞事业;不要系统流、不要金手指", None),
    ("D-仙侠热血", "", "仙侠热血"),
    ("E-完全没头绪", "", None),
]

REVISION_CASES = [
    ("R1-换主角性别+调基调", "主角换成女性,基调再冷一点"),
    ("R2-换世界观体系", "不要门派修炼体系,改成江湖手艺人的行当竞争"),
]


def _bigrams(s: str) -> set[str]:
    t = "".join(ch for ch in str(s) if not ch.isspace())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def plans_similarity(p1: dict, p2: dict) -> float:
    """两套方案文本的二元组 Jaccard:越低差异越大。"""
    s1 = _bigrams("".join(str(p1.get(f, "")) for f in FIELDS))
    s2 = _bigrams("".join(str(p2.get(f, "")) for f in FIELDS))
    return _jaccard(s1, s2)


def render_plan(p: dict, idx: int) -> str:
    lines = [f"**方案 {idx + 1}** `{p.get('label', '')}`"]
    names = {
        "title": "书名", "kernel": "内核", "protagonist": "主角小传",
        "world": "世界观", "arc": "首卷走向", "engine": "连载引擎", "flavor": "味道",
    }
    for k, label in names.items():
        v = p.get(k, "")
        if isinstance(v, list):
            v = " / ".join(str(x) for x in v)
        lines.append(f"- **{label}**:{v}")
    return "\n".join(lines)


async def run_plans(adapter, name: str, topic: str, genre: str | None, report: list[str]):
    boundary = GENRE_STRICT if genre else GENRE_FREE
    topic_block = topic.strip() or "(空白——按你的判断给方向)"
    if genre:
        topic_block += f"\n[已选题材: {genre}]"
    prompt = BOOK_PLANS_PROMPT.format(topic=topic_block, genre_boundary=boundary)
    t0 = time.time()
    try:
        raw = await adapter.ask(prompt)
    except Exception as exc:  # noqa: BLE001
        report.append(f"## {name}\n\n❌ 调用失败:{exc}\n")
        return None
    secs = round(time.time() - t0, 1)
    try:
        data = parse_llm_json(raw)
        plans = data.get("plans") or []
        ok = len(plans) == 3 and all(all(str(p.get(f, "")).strip() for f in FIELDS if f != "flavor") and p.get("flavor") for p in plans)
    except Exception:  # noqa: BLE001
        report.append(f"## {name}\n\n❌ JSON 解析失败(耗时 {secs}s,长度 {len(raw)})\n\n```{raw[:800]}\n```\n")
        return None
    # 指标
    sims = [
        round(plans_similarity(plans[i], plans[j]), 3)
        for i in range(3) for j in range(i + 1, 3)
    ]
    avg_len = round(sum(len(str(p.get(f, ""))) for p in plans for f in FIELDS) / 3)
    labels = [str(p.get("label", "")) for p in plans]
    report.append(
        f"## {name}\n\n"
        f"耗时 {secs}s · 输出 {len(raw)} 字 · JSON 一次解析 {'✓' if ok else '△ 字段有缺'} · "
        f"套均内容 {avg_len} 字 · 三套两两相似度 {sims} (越低越不同) · "
        f"label 唯一 {'✓' if len(set(labels)) == 3 else '✗'}\n\n"
        + "\n\n".join(render_plan(p, i) for i, p in enumerate(plans))
        + "\n"
    )
    print(f"[plans] {name}: {secs}s ok={ok} sims={sims}")
    return plans


async def run_revision(fast, name: str, directive: str, plan: dict, report: list[str]):
    plan_json = plan if isinstance(plan, str) else plan
    prompt = REVISE_PLAN_PROMPT.format(plan_json=plan_json, directive=directive)
    t0 = time.time()
    try:
        raw = await fast.ask(prompt)
        revised = parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001
        report.append(f"## {name}\n\n❌ 失败:{exc}\n")
        return None
    secs = round(time.time() - t0, 1)
    changed = [f for f in FIELDS if str(revised.get(f, "")) != str(plan.get(f, ""))]
    report.append(
        f"## {name}\n\n"
        f"修改要求:「{directive}」\n\n耗时 {secs}s · 改动字段:{changed or '(无)'}\n\n"
        + render_plan(revised, 0)
        + "\n\n<details><summary>原方案</summary>\n\n" + render_plan(plan, 0) + "\n</details>\n"
    )
    print(f"[revise] {name}: {secs}s changed={changed}")
    return revised


async def main() -> None:
    current_user_id.set(1)
    adapter = get_adapter_for(Task.ARCHITECTURE)
    fast = get_adapter_for(Task.POLISH, max_tokens=4096)  # POLISH 属 FAST 档,借道做定向修订
    report = ["# 整书方案×3 去险实验(docs/22 P0 前置验证)", f"\n模型: ARCHITECTURE 档 / FAST 档(修订) · 时间: {time.strftime('%Y-%m-%d %H:%M')}\n"]
    pool: dict[str, list] = {}
    for name, topic, genre in PLAN_CASES:
        plans = await run_plans(adapter, name, topic, genre, report)
        if plans:
            pool[name] = plans
    # 修订:R1 用 A 的方案一;R2 用 D 的方案一
    for (rname, directive), src in zip(REVISION_CASES, ["A-镖师一句话", "D-仙侠热血"]):
        if src in pool:
            await run_revision(fast, rname, directive, pool[src][0], report)
    # A vs A2 方差
    if "A-镖师一句话" in pool and "A2-同输入方差" in pool:
        cross = [round(plans_similarity(p1, p2), 3) for p1 in pool["A-镖师一句话"] for p2 in pool["A2-同输入方差"]]
        report.append(f"## 方差(A vs A2 跨轮相似度)\n\n{cross}(同输入两轮,应明显低于单轮内三套的重叠基准线)\n")
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"done -> {RESULT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
