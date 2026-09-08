"""把 po_feng_ji 夹具从 10 章滚动扩展到 50 章,产出压测夹具。

用法:
    EVAL_API_KEY=... EVAL_FORMAT=deepseek EVAL_BASE_URL=https://api.deepseek.com \
    EVAL_MODEL=deepseek-v4-flash python scripts/extend_fixture_50.py

纯生成不落库:蓝图 generate_blueprint 不碰 DB,产物合并进夹具 dict
另存 app/evals/fixtures/po_feng_ji_50.json。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evals.fixtures import load_fixture  # noqa: E402
from app.evals.runner import ModelPlan  # noqa: E402
from app.engines.pipeline.blueprint import generate_blueprint  # noqa: E402
from app.schemas.tendency import Tendency  # noqa: E402

FX_NAME = "po_feng_ji"
TOTAL = 50
OUT = Path(__file__).resolve().parents[1] / "app/evals/fixtures/po_feng_ji_50.json"

# 章内字段 → 蓝图正文行格式(_format_hint 口径),用于构造 previous_tail
_FIELD_MAP = [
    ("chapter_role", "本章定位"),
    ("chapter_purpose", "核心作用"),
    ("suspense_level", "悬念密度"),
    ("foreshadowing", "伏笔操作"),
    ("plot_twist_level", "认知颠覆"),
    ("characters_involved", "涉及人物"),
    ("key_items", "关键道具"),
    ("scene_location", "场景地点"),
    ("summary", "本章简述"),
    ("beats", "本章节拍"),
]


def outline_to_text(o: dict) -> str:
    lines = [f"第{o['chapter_number']}章 - {o.get('title', '')}"]
    for key, label in _FIELD_MAP:
        v = o.get(key, "")
        if isinstance(v, list):
            v = ";".join(str(x) for x in v)
        lines.append(f"{label}: {v}")
    return "\n".join(lines)


def main() -> int:
    fx = load_fixture(FX_NAME)
    tail = "\n\n".join(outline_to_text(o) for o in fx.outlines[-3:])

    arch = fx.architecture
    arch_text = (
        f"【核心创意】\n{arch.get('core_seed', '')}\n\n"
        f"【人物关系】\n{arch.get('character_dynamics', '')}\n\n"
        f"【世界观】\n{arch.get('world_building', '')}\n\n"
        f"【情节架构】\n{arch.get('plot_architecture', '')}\n\n"
        f"【硬性设定公约(不可违背)】\n{fx.world_rules}"
    )

    plan = ModelPlan.from_env()
    if plan is None:
        print("缺 EVAL_API_KEY", file=sys.stderr)
        return 2

    async def run() -> tuple[list[dict], list[str]]:
        with plan.applied():
            return await generate_blueprint(
                novel_architecture=arch_text,
                number_of_chapters=TOTAL,
                start_chapter=len(fx.outlines) + 1,
                end_chapter=TOTAL,
                previous_tail=tail,
                global_tendency=Tendency(**(fx.to_dict().get("global_tendency") or {})),
                progress=lambda s: print(" ", s, flush=True),
            )

    chapters, warnings = asyncio.run(run())
    got = sorted(chapters, key=lambda c: int(c["chapter_number"]))
    print(f"生成 {len(got)} 章(第 {got[0]['chapter_number']}~{got[-1]['chapter_number']} 章)")
    for w in warnings:
        print("警告:", w)

    merged = fx.to_dict()
    merged["name"] = "po_feng_ji_50"
    merged["title"] = "破封纪(50章压测)"
    merged["notes"] = (
        "压测夹具:前 10 章沿用黄金样本蓝图,第 11-50 章由滚动规划生成,"
        "用于 50 章长程一致性/成本/降级压测。生成时 architecture/world_rules 与母夹具一致。"
    )
    merged["outlines"] = list(fx.outlines) + got
    assert all(
        merged["outlines"][i]["chapter_number"] == i + 1 for i in range(TOTAL)
    ), "章号不连续"
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写出 {OUT}(共 {len(merged['outlines'])} 章)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
