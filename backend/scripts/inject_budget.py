# scripts/inject_budget.py
# -*- coding: utf-8 -*-
"""章节草稿 prompt 的「注入预算」盘点(docs/25 §5.1)。

为什么先量再改:检索层改造(全量灌 → 按需取)的全部收益都来自「知道现在到底
灌了多少、谁最大」。没有这张表,任何"应该能省一半"都是拍脑袋;A/B 评测也失去
了对照基线。

两种口径,都能立刻跑:
  A. 静态口径(默认,不需要任何数据):量模板本身的固定指令文本与占位符清单。
     这一部分是**每章都要付**的固定成本,与书无关。
  B. 实书口径(--project/--chapter):走真实组装路径,逐块列出实际字数。
     **只读**:不写库、不调 LLM。

用法:
    python backend/scripts/inject_budget.py
    python backend/scripts/inject_budget.py --project 3 --chapter 12
    python backend/scripts/inject_budget.py --json > inject_budget.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
sys.path.insert(0, str(BACKEND))

PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")
# 分块标记:【xxx】是本仓 prompt 的一贯分块写法,用它切出块边界
BLOCK_HEAD = re.compile(r"【([^】]{1,20})】")

# 这些块是「红线」,任何检索改造都不许裁(docs/25 §5.2 约束①)
NEVER_FILTER = {"硬约束", "章末契约", "上章契约"}

# 源码里出现过的【】块头(f-string 拼名的取 "{" / 冒号前的前缀):
# 模板块 + 运行时构建器打的头都算「结构块」;两者都对不上的标记,
# 多半是占位符内容(世界观/场景卡文本)里自带的【】,不能当独立注入块解读。
_SRC_BLOCK = re.compile(r"【([^】{}]{1,20})】")
_SRC_PREFIX_HINT = re.compile(r"【([^】{}:：]{1,20})")


def structural_block_names() -> set[str]:
    """模板 + 引擎源码里声明的全部结构块头(含 f-string 前缀)。"""
    from app.prompts.chapter import CHAPTER_DRAFT_PROMPT

    names = {m.group(1) for m in BLOCK_HEAD.finditer(CHAPTER_DRAFT_PROMPT)}
    prefixes: list[str] = []
    for base in (APP / "engines", APP / "prompts"):
        for py in base.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            names.update(_SRC_BLOCK.findall(text))
            prefixes.extend(_SRC_PREFIX_HINT.findall(text))
    return names | {p for p in prefixes if p}


def static_report(tpl_name: str = "CHAPTER_DRAFT_PROMPT") -> dict:
    """模板固定文本 + 占位符清单。不碰数据库。"""
    from app.prompts import chapter as ch

    body = getattr(ch, tpl_name)
    # 不要在 {skill_block} 处截断:它在模板靠前位置,截了会把后面一半块丢掉,
    # 盘点表就会少报二十来块——盘点工具自己少报,比没有更坏。
    fixed = PLACEHOLDER.sub("", body)
    phs = PLACEHOLDER.findall(body)
    blocks = [(m.group(1), m.start()) for m in BLOCK_HEAD.finditer(body)]
    sizes = []
    for i, (name, start) in enumerate(blocks):
        end = blocks[i + 1][1] if i + 1 < len(blocks) else len(body)
        sizes.append((name, end - start))
    sizes.sort(key=lambda kv: kv[1], reverse=True)
    return {
        "template": tpl_name,
        "total_chars": len(body),
        "fixed_chars": len(fixed),
        "placeholder_count": len(set(phs)),
        "placeholder_occurrences": len(phs),
        "placeholders": sorted(set(phs)),
        "block_count": len(blocks),
        "blocks_by_size": sizes,
    }


def book_report(project_id: int, chapter_number: int, session=None) -> dict:
    """实书口径:走真实组装路径量每一块。失败就如实报错,绝不编数字。

    session 可注入(测试用内存库);默认开自己的 SessionLocal。
    """
    import asyncio

    import app.engines.pipeline.chapter as chapter_mod
    from app.db.models import Project
    from app.db.session import SessionLocal
    from app.engines.common import get_outline
    from app.engines.pipeline.chapter_compose import Composer

    # 写前审核是阶段 1 里唯一的 LLM 调用,而且只警告不阻断、警告不进草稿 prompt
    # (还会把警告写库)。盘点工具打桩跳过,守住「只读、不调 LLM」的承诺;
    # 其余组装步骤全部是纯读 + 确定性推导(见 _prepare_chapter_context 的 docstring)。
    # 用完还原,不污染同进程里真正要跑写前审核的调用方(测试/服务)。
    async def _skip_preflight(*_args, **_kwargs) -> list:  # noqa: ANN002
        return []

    _orig_preflight = chapter_mod.preflight_chapter
    chapter_mod.preflight_chapter = _skip_preflight

    async def _draft_prompt(session, project):
        outline = get_outline(session, project.id, chapter_number)
        if outline is None:
            return None
        ctx = await chapter_mod._prepare_chapter_context(  # noqa: SLF001 — 盘点工具走真实组装路径
            session, project, chapter_number, outline=outline
        )
        composer = Composer(ctx.compose_context(project=project, db=session))
        return composer._draft_prompt("")  # noqa: SLF001 — 复用首轮流写的同一份拼装

    out: dict = {"project_id": project_id, "chapter_number": chapter_number}
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        if project is None:
            return {"error": f"没有这个项目:{project_id}"}
        prompt = asyncio.run(_draft_prompt(session, project))
        if prompt is None:
            return {"error": f"这本书没有第 {chapter_number} 章的蓝图(大纲),无法组装草稿上下文"}
        blocks = [(m.group(1), m.start()) for m in BLOCK_HEAD.finditer(prompt)]
        sizes = []
        for i, (name, start) in enumerate(blocks):
            end = blocks[i + 1][1] if i + 1 < len(blocks) else len(prompt)
            sizes.append((name, end - start))
        sizes.sort(key=lambda kv: kv[1], reverse=True)
        # 切块正则会命中**数据内部**携带的【】标记(如世界观文本里的「关联场景」),
        # 这类"块"其实是某个占位符内容的中段,不能当成独立注入块解读——
        # 用「模板 + 引擎源码声明过的块头」做白名单,剩下的单独标出,防止读表人误判。
        known = structural_block_names()
        data_internal = sorted({
            n for n, _ in sizes
            if n not in known and not any(n.startswith(p) for p in known)
        })
        return {
            **out,
            "total_chars": len(prompt),
            "block_count": len(blocks),
            "blocks_by_size": sizes,
            "data_internal_blocks": data_internal,
            "never_filter_blocks": [n for n, _ in sizes if n in NEVER_FILTER],
        }
    finally:
        chapter_mod.preflight_chapter = _orig_preflight
        if own_session:
            session.close()


def render(data: dict) -> str:
    L: list[str] = ["=" * 72]
    if "error" in data:
        return "实书口径没跑通:%s" % data["error"]
    if "total_chars" in data and "project_id" in data:
        L.append("实书口径 — 项目 %s 第 %s 章" % (data["project_id"], data["chapter_number"]))
        L.append("草稿 prompt 总字数:%d / 块数:%d"
                 % (data["total_chars"], data["block_count"]))
        L.append("红线块(检索改造不许裁):%s"
                 % (", ".join(data["never_filter_blocks"]) or "(未见标记块)"))
        L.append("数据内标记(占位符内容里带的【】,不是独立注入块):%s"
                 % (", ".join(data.get("data_internal_blocks", ())) or "(无)"))
        L.append("-" * 72)
        internal = set(data.get("data_internal_blocks", ()))
        for name, size in data["blocks_by_size"][:25]:
            mark = "†" if name in internal else " "
            L.append(" %s%-20s %6d 字" % (mark, name, size))
    else:
        L.append("静态口径 — 模板 %s" % data["template"])
        L.append("模板总长:%d 字 = 固定指令 %d + %d 个内容占位符(%d 次出现) · 可切块:%d 块"
                 % (data["total_chars"], data["fixed_chars"],
                    data["placeholder_count"], data["placeholder_occurrences"],
                    data["block_count"]))
        L.append("-" * 72)
        for name, size in data["blocks_by_size"][:20]:
            L.append("  %-18s %6d 字" % (name, size))
        L.append("-" * 72)
        L.append("占位符清单(每个都是一块「按书内容」的成本):")
        for i in range(0, len(data["placeholders"]), 6):
            L.append("  " + "  ".join(data["placeholders"][i:i + 6]))
    L.append("=" * 72)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=int, help="项目 id(给了就跑实书口径)")
    ap.add_argument("--chapter", type=int, help="章号")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.project and args.chapter:
        data = book_report(args.project, args.chapter)
    else:
        data = static_report()
        if args.project or args.chapter:
            print("提示:--project/--chapter 要成对给,这次按静态口径输出。", file=sys.stderr)

    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(render(data))
    return 1 if data.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
