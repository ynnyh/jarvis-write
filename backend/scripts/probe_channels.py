# scripts/probe_channels.py
# -*- coding: utf-8 -*-
"""批量渠道体检:一批中转渠道里挑出真正能写中文长篇的那个。

为什么需要它:中转渠道「能不能连通」和「能不能干活」是两回事——
2026-09-08 实测某渠道 ping 秒过、小请求正常,真跑管线却把英文思维链吐进正文,
JSON 环节全线解析失败,整轮压测白烧。这个脚本用与产品「测试连接」同一套体检
(中文输出 / JSON 结构 / 响应速度)逐个渠道过一遍,直接出结论表。

用法:
    python scripts/probe_channels.py channels.json

channels.json 格式(列表,字段见下;api_key 建议从环境变量注入,不落盘):
    [
      {"name": "快跑", "base_url": "https://x.ai/v1",
       "model": "glm-5.3-flash", "api_key": "sk-xxx"}
    ]

输出:每个渠道一行(是否适配 / 各项结论 / 速度),末尾给「可用渠道」清单。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 体检要用产品侧真实适配器(含流式优先/重试/空正文归因),所以必须先把
# DATABASE_URL 指到一个临时库,别碰开发库。
os.environ.setdefault("DATABASE_URL", "sqlite:///probe_channels_tmp.db")

from app.llm.factory import create_llm_adapter  # noqa: E402
from app.llm.probe import probe_channel  # noqa: E402


async def probe_one(ch: dict) -> dict:
    name = ch.get("name") or ch.get("base_url") or "?"
    try:
        adapter = create_llm_adapter(
            provider=ch.get("interface_format") or "openai-compatible",
            api_key=ch.get("api_key") or "",
            base_url=ch.get("base_url") or "",
            model_name=ch.get("model") or "",
            max_tokens=1024,
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ok": False, "suitable": False, "note": f"建适配器失败:{exc}"}

    try:
        probe = await probe_channel(adapter)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ok": False, "suitable": False, "note": f"体检异常:{exc}"}

    return {
        "name": name,
        "ok": probe.ok,
        "suitable": probe.suitable,
        "tps": round(probe.tokens_per_second, 1),
        "checks": [
            {"name": c.name, "passed": c.passed, "warning": c.warning,
             "detail": c.detail, "sample": c.sample}
            for c in probe.checks
        ],
    }


def _line(r: dict) -> str:
    if r.get("note"):
        return f"  ✗ {r['name']}: {r['note']}"
    verdict = "✓ 可用" if r["suitable"] else ("△ 通但不适配" if r["ok"] else "✗ 连不通")
    checks = " · ".join(
        f"{'✓' if c['passed'] else ('⚠' if c['warning'] else '✗')}{c['name']}"
        for c in r.get("checks", [])
    )
    return f"  {verdict}  {r['name']}  [{checks}]  {r.get('tps', 0)} tok/s"


async def main(path: Path, verbose: bool) -> int:
    channels = json.loads(path.read_text(encoding="utf-8"))
    print(f"体检 {len(channels)} 个渠道…\n")
    results = []
    for ch in channels:
        r = await probe_one(ch)
        results.append(r)
        print(_line(r))
        if verbose:
            for c in r.get("checks", []):
                if not c["passed"]:
                    print(f"      - {c['name']}: {c['detail']}")
                    if c.get("sample"):
                        print(f"        原样回复: {c['sample']}")
    usable = [r["name"] for r in results if r.get("suitable")]
    print("\n可用渠道(适合中文长篇写作):" + ("、".join(usable) if usable else "无"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="批量渠道体检")
    ap.add_argument("channels", type=Path, help="渠道清单 JSON")
    ap.add_argument("--verbose", action="store_true", help="打印失败项的原因与原样回复")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.channels, args.verbose)))
