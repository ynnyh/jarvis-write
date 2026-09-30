# scripts/skill_line_audit.py
# -*- coding: utf-8 -*-
"""出片线「Skill 包 + 质量关卡」五件套盘点。

为什么存在:docs/25 说三条出片线要补质量关卡,但"到底缺哪几个零件"此前
全靠人读代码拍脑袋——抽读会漏,也会把「归一化函数」误认成「校验器」。
本脚本把这件事变成可复算的输出:线 × 零件 → 文件:行 或 缺失。

用法:
    python backend/scripts/skill_line_audit.py            # 人读表格
    python backend/scripts/skill_line_audit.py --json     # 机器读(门禁/CI 可用)

只读:不碰数据库,不调 LLM,不改任何文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
ENGINES = APP / "engines"

# 盘点范围:三条出片线 + 作为样板的动漫线(anime 是五件套的参考实现)
LINES = {
    "anime": ENGINES / "anime",      # 样板:镜头卡工艺
    "drama": ENGINES / "drama",      # 本期主攻
    "promo": ENGINES / "promo",
    "clips": ENGINES / "clips",
}

# 五件套。每一项的正则都要能区分「真有」与「同名不同义」——
# 例如 _norm_shots 是数据归一化,不是契约校验,所以校验项只认 validate。
PARTS = {
    "包控开关": r"def \w*pack_on\b|def active_\w+_render\b",
    "契约校验": r"def validate_\w+|def _validate_\w+",
    "定向重试": r"REPAIR_INSTRUCTION|def \w*repair\w*\(|\w*崩坏\w*",
    "负面基座": r"NEGATIVE_BASE|negative_base|负面基座",
    "版本快照": r"def push_version|def version_list|def find_version",
    "Skill 挂载": r"render_skill_block|render_project_skill_block|active_packs\(",
}

# 「提示词槽」扫的是 app/prompts/(模板槽在模板里,不在引擎里)——
# 与上面五件套不同源,故单列一份按线归组的模板清单。
SLOT_KEYS = {
    "anime": ["ANIME_SHOTS_PROMPT", "ANIME_SEGMENT_PROMPT_TEMPLATE", "ANIME_SHOTCARD_PROMPT"],
    "drama": ["EPISODE_PLAN_PROMPT", "EPISODE_SCRIPT_PROMPT", "STORYBOARD_PROMPT",
              "SEGMENTED_FILM_PROMPT_TEMPLATE", "SHOT_PROMPT_PROMPT"],
    "promo": ["PROMO_BRIEF_PROMPT", "PROMO_SCRIPT_PROMPT", "PROMO_STORYBOARD_PROMPT",
              "SEGMENTED_FILM_PROMPT_TEMPLATE", "PROMO_SHOT_PROMPT_PROMPT"],
    "clips": ["CLIPS_TAKES_PROMPT", "CLIPS_EXPAND_PROMPT", "WHOLE_CLIP_PROMPT_TEMPLATE"],
}

# 提示词模板的挂载点(PROMPT 常量 .format 处),用于数「这个线到底有几个工序」
FMT = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\.format\(")


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _prompts_src() -> str:
    """模板全量文本:app/prompts/ 下所有 py 拼起来(常量定义分散在各文件)。"""
    root = APP / "prompts"
    if not root.is_dir():
        return ""
    return "\n".join(p.read_text(encoding="utf-8") for p in _files(root))


def audit() -> dict:
    out: dict = {"lines": {}, "templates": {}, "slots": {}}
    prompts_src = _prompts_src()
    for name, root in LINES.items():
        if not root.is_dir():
            out["lines"][name] = {"_missing_dir": str(root)}
            continue
        srcs = {f: f.read_text(encoding="utf-8") for f in _files(root)}
        hits: dict[str, list[str]] = {}
        for part, rx in PARTS.items():
            found: list[str] = []
            for f, src in srcs.items():
                for i, line in enumerate(src.splitlines(), 1):
                    if re.search(rx, line):
                        rel = f.relative_to(APP).as_posix()
                        found.append(f"{rel}:{i}")
                        break  # 每零件每线只报第一个,够定位即可
            hits[part] = found
        out["lines"][name] = hits

        # 该线实际使用的提示词模板(工序数)
        names: set[str] = set()
        for src in srcs.values():
            names.update(FMT.findall(src))
        out["templates"][name] = sorted(names)

        # 模板槽:这批模板里哪些已经声明 {skill_block}
        slots: dict[str, bool] = {}
        for tpl in SLOT_KEYS.get(name, []):
            m = re.search(rf"^{tpl}\s*=\s*\"\"\"(.*?)\"\"\"", prompts_src, re.S | re.M)
            slots[tpl] = bool(m and "{skill_block}" in m.group(1))
        out["slots"][name] = slots
    return out


def render_text(data: dict) -> str:
    # 标记用中文而非 emoji:Windows 控制台默认 GBK,emoji 会 UnicodeEncodeError
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("出片线五件套盘点(有=已具备,报首个命中位置 / 缺=没有)")
    lines.append("=" * 78)
    head = "零件".ljust(12) + "".join(n.ljust(14) for n in data["lines"])
    lines.append(head)
    lines.append("-" * 78)
    for part in PARTS:
        row = part + " " * max(1, 12 - len(part))
        for name in data["lines"]:
            hits = data["lines"][name].get(part, [])
            cell = "有 " + hits[0].split("/")[-1] if hits else "缺"
            row += cell.ljust(14)
        lines.append(row)
    lines.append("-" * 78)
    for name, tpls in data["templates"].items():
        lines.append(f"{name:8} 工序模板 {len(tpls)} 个")
    lines.append("-" * 78)
    lines.append("模板槽 {skill_block}(槽=已声明可注入):")
    for name, slots in data["slots"].items():
        for tpl, has in slots.items():
            lines.append(f"  {name:8} {'槽' if has else '无'}  {tpl}")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    data = audit()
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(render_text(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
