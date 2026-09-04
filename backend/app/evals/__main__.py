# app/evals/__main__.py
# -*- coding: utf-8 -*-
"""评测底座命令行(在 backend/ 下执行 `python -m app.evals <子命令>`)。

  prompts   打印全部 prompt 的内容指纹;--save 存档,--diff 与存档比对(改了哪几条)
  fixtures  列出内置黄金样本书
  score     对任意正文文件算确定性指标(零 LLM,秒回)
  export    把库里一本书的架构 + 前 N 章蓝图导成夹具 JSON
  run       灌夹具、真跑管线逐章生成、落 JSON(要 key;默认用独立的临时库)
  compare   两份 run JSON 出对比表

`run` 的库:默认在 --out-dir 下新建一个独立 SQLite,绝不碰你的 jarvis_write.db;
显式 --db 指向已有库时,库里若存在非「[评测] 」前缀的项目会拒跑(--force 才放行)。
DATABASE_URL 必须在导入 app.* 之前设好(引擎在导入时就建),所以 run 里的 app 导入全部延迟。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _utf8_console() -> None:
    # Windows 控制台默认 GBK,中文与 ✅/❌ 会编崩
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _db_url(value: str) -> str:
    if "://" in value:
        return value
    return f"sqlite:///{Path(value).resolve().as_posix()}"


# ---------- prompts ----------
def cmd_prompts(args: argparse.Namespace) -> int:
    from app.evals.prompt_registry import diff_manifests, manifest_fingerprint, prompt_manifest

    manifest = prompt_manifest()
    fingerprint = manifest_fingerprint(manifest)
    if args.save:
        Path(args.save).write_text(
            json.dumps({"fingerprint": fingerprint, "manifest": manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已存档 {len(manifest)} 条 prompt 指纹 → {args.save}")
    if args.diff:
        saved = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        old = saved.get("manifest") or saved
        diff = diff_manifests(old, manifest)
        total = sum(len(v) for v in diff.values())
        if not total:
            print(f"与 {args.diff} 一致:{len(manifest)} 条 prompt 无变动(指纹 {fingerprint})")
        else:
            print(f"与 {args.diff} 相比有 {total} 处变动:")
            for kind, label in (("changed", "改动"), ("added", "新增"), ("removed", "删除")):
                for name in diff[kind]:
                    print(f"  {label}  {name}")
        return 0
    if args.json:
        print(json.dumps({"fingerprint": fingerprint, "manifest": manifest}, ensure_ascii=False, indent=2))
    else:
        print(f"prompt 总指纹 {fingerprint}({len(manifest)} 条)")
        for name, digest in manifest.items():
            print(f"  {digest}  {name}")
    return 0


# ---------- fixtures ----------
def cmd_fixtures(_args: argparse.Namespace) -> int:
    from app.evals.fixtures import list_fixtures, load_fixture

    names = list_fixtures()
    if not names:
        print("没有内置夹具")
        return 0
    for name in names:
        fx = load_fixture(name)
        print(f"{name:20s} 《{fx.title}》 {fx.genre} · {fx.chapter_count} 章 · 每章约 {fx.target_words} 字")
        if fx.notes:
            print(f"{'':20s} {fx.notes}")
    return 0


# ---------- score ----------
def cmd_score(args: argparse.Namespace) -> int:
    from app.evals.metrics import cross_chapter_metrics, text_metrics

    texts: list[str] = []
    rows: list[dict] = []
    for file in args.files:
        text = Path(file).read_text(encoding="utf-8")
        texts.append(text)
        rows.append({"file": file, **text_metrics(text, args.target_words)})
    cross = cross_chapter_metrics(texts) if len(texts) >= 2 else None
    if args.json:
        print(json.dumps({"files": rows, "cross": cross}, ensure_ascii=False, indent=2))
        return 0
    print("| 文件 | 字数 | 篇幅比 | 段 | 对白占比 | 章内复读 | AI 味 | 主要命中 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        fl = r["flavor"]
        top = "、".join(f"{k}×{v}" for k, v in sorted(fl["categories"].items(), key=lambda kv: -kv[1])[:3])
        ratio = r.get("target_ratio")
        print(
            f"| {r['file']} | {r['chars']} | {ratio if ratio is not None else '—'} | {r['paragraphs']} "
            f"| {r['dialogue_ratio']} | {r['within_repeats']} | {fl['score']} | {top or '—'} |"
        )
    if cross:
        print(f"\n跨文件重复句 {cross['repeated_sentences']} 条,高频短语 {cross['repeated_phrases']} 条")
        for t in cross["repeated_sentences_top"]:
            print(f"  ×{t['count']}  {t['text']}")
    return 0


# ---------- export ----------
def cmd_export(args: argparse.Namespace) -> int:
    if args.db:
        os.environ["DATABASE_URL"] = _db_url(args.db)
    from app.db.session import SessionLocal
    from app.evals.fixtures import export_project, save_fixture

    with SessionLocal() as db:
        data = export_project(db, args.project_id, chapters=args.chapters, name=args.name)
    out = args.out or f"{data['name']}.json"
    path = save_fixture(data, out)
    print(f"已导出夹具《{data['title']}》{len(data['outlines'])} 章 → {path}")
    return 0


# ---------- run ----------
def _plan_from_args(args: argparse.Namespace):
    from app.evals.runner import ModelPlan

    env_plan = ModelPlan.from_env()
    if not (args.api_key or args.model or args.base_url):
        return env_plan
    base = env_plan or ModelPlan(interface_format="openai-compatible", api_key="", base_url="", model="")
    return ModelPlan(
        interface_format=args.format or base.interface_format,
        api_key=args.api_key or base.api_key,
        base_url=args.base_url or base.base_url,
        model=args.model or base.model,
        fast_model=args.fast_model or base.fast_model,
        review_model=args.review_model or base.review_model,
    )


def cmd_run(args: argparse.Namespace) -> int:
    import asyncio

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = args.label or f"run-{stamp}"

    if args.db:
        os.environ["DATABASE_URL"] = _db_url(args.db)
    else:
        os.environ["DATABASE_URL"] = f"sqlite:///{(out_dir / f'{label}-{stamp}.db').as_posix()}"
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
    if "app.db.session" in sys.modules:
        print("⚠️ app.db.session 已在本进程导入,DATABASE_URL 覆盖无效——请用独立进程运行 run", file=sys.stderr)
        return 2

    from app.db.models import Project
    from app.db.session import SessionLocal, engine
    from app.db.base import Base
    from app.evals import report
    from app.evals.fixtures import TITLE_PREFIX, load_fixture
    from app.evals.runner import describe_models, run_fixture, save_run

    Base.metadata.create_all(bind=engine)
    if args.db and not args.force:
        with SessionLocal() as db:
            real = db.query(Project).filter(~Project.title.startswith(TITLE_PREFIX)).count()
        if real:
            print(f"库 {args.db} 里有 {real} 本非评测项目,拒绝在真书库上跑评测(确认无误加 --force)", file=sys.stderr)
            return 2

    fx = load_fixture(args.fixture)
    plan = _plan_from_args(args)
    models = describe_models(plan)
    if not any(isinstance(m, dict) and m.get("has_key") for m in models.values()):
        print(
            "没有可用的模型 key:请在 .env 配 provider key,或用 --api-key/--base-url/--model/--format"
            "(或 EVAL_API_KEY 等环境变量)指定评测模型",
            file=sys.stderr,
        )
        return 2
    print(f"模型:{json.dumps(models, ensure_ascii=False)}")
    print(f"库:{os.environ['DATABASE_URL']}")

    run = asyncio.run(
        run_fixture(
            fx,
            chapters=args.chapters,
            label=label,
            plan=plan,
            progress=print,
            verbose_stages=args.verbose,
        )
    )
    json_path, text_path = save_run(run, out_dir)
    print()
    print(report.run_markdown(run))
    print(f"结果:{json_path}")
    if text_path:
        print(f"正文:{text_path}")
    agg = run.get("aggregate") or {}
    return 0 if agg.get("chapters_ok") == agg.get("chapters_total") else 1


# ---------- compare ----------
def cmd_compare(args: argparse.Namespace) -> int:
    from app.evals import report
    from app.evals.runner import load_run

    md = report.compare_markdown(load_run(args.a), load_run(args.b))
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"对比表已写入 {args.out}")
    print(md)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evals", description="jarvis-write 生成质量评测底座"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prompts", help="prompt 内容指纹清单")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save", help="把清单存成 JSON 文件")
    p.add_argument("--diff", help="与存档的清单比对,列出变动的 prompt")
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("fixtures", help="列出内置黄金样本书")
    p.set_defaults(func=cmd_fixtures)

    p = sub.add_parser("score", help="对正文文件算确定性指标(零 LLM)")
    p.add_argument("files", nargs="+")
    p.add_argument("--target-words", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("export", help="从库里一本书导出夹具")
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--chapters", type=int, default=None, help="只导前 N 章蓝图")
    p.add_argument("--name", default=None)
    p.add_argument("--out", default=None, help="输出路径,默认 <name>.json")
    p.add_argument("--db", default=None, help="库路径或 URL,默认 .env 的 DATABASE_URL")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("run", help="灌夹具真跑管线,落评测 JSON")
    p.add_argument("--fixture", required=True, help="内置夹具名或 JSON 路径")
    p.add_argument("--chapters", type=int, default=None, help="只跑前 N 章")
    p.add_argument("--label", default=None, help="这轮的名字(如 baseline / draft-v2)")
    p.add_argument("--out-dir", default="evals_out")
    p.add_argument("--db", default=None, help="用已有库(默认新建独立临时库)")
    p.add_argument("--force", action="store_true", help="允许在含真书的库上跑")
    p.add_argument("--format", default=None, help="openai-compatible / anthropic / gemini / deepseek / openai")
    p.add_argument("--api-key", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--model", default=None, help="quality 档模型")
    p.add_argument("--fast-model", default=None, help="fast 档模型(缺省同 quality)")
    p.add_argument("--review-model", default=None, help="review 档模型(缺省同 quality)")
    p.add_argument("--verbose", action="store_true", help="打印每章的六段进度")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("compare", help="两份 run JSON 出对比表")
    p.add_argument("a", help="基线 run JSON")
    p.add_argument("b", help="新 run JSON")
    p.add_argument("--out", default=None, help="把 Markdown 写到文件")
    p.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
