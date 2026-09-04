# app/evals/runner.py
# -*- coding: utf-8 -*-
"""评测 runner:把黄金样本书灌进当前库,真跑 generate_chapter 逐章生成,把每章的数字收进一份 JSON。

跑的是线上同一条管线(写前审核 → 草稿 → 定稿 → 门禁 → 抽取 → 摘要 → 契约),不是
简化版——简化版测出来的数字不代表用户看到的东西。逐章顺序生成,圣经 / 滚动摘要 /
章末契约随章累积,长程一致性那一路也在被测。

模型来源二选一:
- 不指定(plan=None):走当前进程能解析到的配置(.env,或已登录用户的库配置);
- ModelPlan:显式三档模型 + key,通过 `applied()` 临时接管适配器工厂——评测想固定
  「用哪个模型」就用它,结果 JSON 里只记模型名与 host,绝不落 key。

每章失败不中断(记录 error 继续),但要知道:失败章之后的章节缺了上文,数字要打折看。
"""
from __future__ import annotations

import os
import subprocess
import time
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import func

from app.db.base import Base
from app.db.models import Chapter, Entity, Fact, Foreshadowing, LlmUsage, Project
from app.db.session import SessionLocal, engine
from app.engines.pipeline.chapter import generate_chapter
from app.evals.fixtures import Fixture, seed_fixture
from app.evals.metrics import cross_chapter_metrics, text_metrics
from app.evals.prompt_registry import manifest_fingerprint, prompt_manifest

RUN_SCHEMA = 1
TIERS = ("quality", "fast", "review")
_SCORE_DIMS = ("plot", "prose", "pacing", "character", "continuity")


# =============== 模型接管 ===============
@dataclass
class ModelPlan:
    """评测用的显式模型配置(quality / fast / review 三档,后两档缺省跟随主档)。"""

    interface_format: str
    api_key: str
    base_url: str
    model: str
    fast_model: str | None = None
    review_model: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ModelPlan | None":
        """EVAL_API_KEY / EVAL_BASE_URL / EVAL_MODEL / EVAL_FORMAT(/ EVAL_FAST_MODEL /
        EVAL_REVIEW_MODEL);没给 key 就返回 None(走当前进程默认配置)。"""
        env = dict(os.environ if env is None else env)
        key = (env.get("EVAL_API_KEY") or "").strip()
        if not key:
            return None
        return cls(
            interface_format=(env.get("EVAL_FORMAT") or "openai-compatible").strip(),
            api_key=key,
            base_url=(env.get("EVAL_BASE_URL") or "").strip(),
            model=(env.get("EVAL_MODEL") or "").strip(),
            fast_model=(env.get("EVAL_FAST_MODEL") or "").strip() or None,
            review_model=(env.get("EVAL_REVIEW_MODEL") or "").strip() or None,
        )

    def _cfg(self, config_id: int, model: str) -> dict[str, Any]:
        return {
            "id": config_id,
            "name": f"评测·{model}",
            "interface_format": self.interface_format,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": model,
            "timeout": 0,
            "max_tokens": 0,
            "thinking_mode": "",
            "is_default": config_id == -1,
            "is_default_fast": config_id == -2,
            "is_default_review": config_id == -3,
        }

    def tier_configs(self) -> dict[str, dict[str, Any]]:
        return {
            "quality": self._cfg(-1, self.model),
            "fast": self._cfg(-2, self.fast_model or self.model),
            "review": self._cfg(-3, self.review_model or self.model),
        }

    @contextmanager
    def applied(self):
        """把三档配置临时接进适配器工厂(不碰数据库、不读 .env)。

        router._tier_config 每次调用都从 factory 取 resolve_tier_config;
        create_llm_adapter(config_id=…) 在 factory 内部查 get_config_by_id——两处都是
        factory 的模块全局名,替换它们即可覆盖全部任务档位;退出时原样还回去。
        负数 id 是刻意的:库里的配置 id 永远为正,撞不上。
        """
        from app.llm import factory

        cfgs = self.tier_configs()
        by_id = {c["id"]: c for c in cfgs.values()}
        orig_resolve, orig_by_id = factory.resolve_tier_config, factory.get_config_by_id

        def _resolve(tier: str = "quality") -> dict[str, Any]:
            return cfgs.get(tier, cfgs["quality"])

        def _by_id(config_id: int) -> dict[str, Any]:
            if config_id in by_id:
                return by_id[config_id]
            return orig_by_id(config_id)

        factory.resolve_tier_config, factory.get_config_by_id = _resolve, _by_id
        try:
            yield
        finally:
            factory.resolve_tier_config, factory.get_config_by_id = orig_resolve, orig_by_id


def _host(url: str | None) -> str:
    try:
        return urlparse(url or "").netloc or (url or "")
    except ValueError:
        return url or ""


def describe_models(plan: ModelPlan | None = None) -> dict[str, Any]:
    """三档各用哪个模型(只记模型名 / host / 来源,不记 key)。"""
    from app.llm import factory

    out: dict[str, Any] = {}
    for tier in TIERS:
        try:
            cfg = plan.tier_configs()[tier] if plan else factory.resolve_tier_config(tier)
        except Exception as exc:  # noqa: BLE001 — 描述配置失败不该让评测跑不起来
            out[tier] = {"error": str(exc)[:120]}
            continue
        out[tier] = {
            "format": cfg.get("interface_format"),
            "model": cfg.get("model"),
            "host": _host(cfg.get("base_url")),
            "source": "eval-plan" if plan else ("db" if cfg.get("id") else "env"),
            "has_key": bool((cfg.get("api_key") or "").strip()),
        }
    return out


def git_commit() -> str:
    repo = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# =============== 逐章记录 ===============
def _numbers_only(value: Any) -> Any:
    """抽取统计里只保留数字(嵌套一层),JSON 友好且不带正文。"""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _extraction_summary(stats: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (stats or {}).items():
        kept = _numbers_only(value)
        if kept not in (None, {}):
            out[key] = kept
    return out


def chapter_record(
    n: int, title: str, result: tuple, target_words: int, seconds: float
) -> dict[str, Any]:
    """generate_chapter 的六元组 → 一章的评测记录(不含正文)。"""
    chapter, gate_issues, extraction, guard, review, preflight = result
    text = chapter.final_content or ""
    tm = text_metrics(text, target_words)
    severities = Counter(str(i.get("severity") or "minor") for i in (gate_issues or []))
    review = review or {}
    scores = {k: v for k, v in (review.get("scores") or {}).items() if v}
    return {
        "n": n,
        "title": title,
        "ok": True,
        "seconds": round(seconds, 1),
        "status": chapter.status,
        "quarantined": chapter.status == "quarantined",
        "chars": tm["chars"],
        "target_ratio": tm.get("target_ratio"),
        "paragraphs": tm["paragraphs"],
        "dialogue_ratio": tm["dialogue_ratio"],
        "within_repeats": tm["within_repeats"],
        "flavor": tm["flavor"],
        "review": {
            "scores": scores,
            "passed": review.get("passed"),
            "revision_rounds": int(review.get("revision_rounds") or 0),
            "repair_rounds": int(review.get("repair_rounds") or 0),
            "comment": str(review.get("comment") or "")[:200],
        },
        "gate": {
            "blocker": severities.get("blocker", 0),
            "major": severities.get("major", 0),
            "minor": severities.get("minor", 0),
        },
        "preflight_warnings": len(preflight or []),
        "guard_action": getattr(guard, "action", "none"),
        "extraction": _extraction_summary(extraction),
    }


def _mean(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(mean(nums), 2) if nums else None


def aggregate(run: dict[str, Any]) -> dict[str, Any]:
    chapters = run.get("chapters") or []
    ok = [c for c in chapters if c.get("ok")]
    n_ok = len(ok)
    usage = run.get("usage") or {}
    total_tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    passed = sum(1 for c in ok if (c.get("review") or {}).get("passed") is True)
    return {
        "chapters_total": len(chapters),
        "chapters_ok": n_ok,
        "quarantined": sum(1 for c in ok if c.get("quarantined")),
        "pass_rate": round(passed / n_ok, 2) if n_ok else None,
        "mean_scores": {
            d: _mean([(c.get("review") or {}).get("scores", {}).get(d) for c in ok])
            for d in _SCORE_DIMS
        },
        "mean_flavor": _mean([(c.get("flavor") or {}).get("score") for c in ok]),
        "mean_target_ratio": _mean([c.get("target_ratio") for c in ok]),
        "mean_revision_rounds": _mean([(c.get("review") or {}).get("revision_rounds") for c in ok]),
        "total_blockers": sum((c.get("gate") or {}).get("blocker", 0) for c in ok),
        "total_major": sum((c.get("gate") or {}).get("major", 0) for c in ok),
        "total_minor": sum((c.get("gate") or {}).get("minor", 0) for c in ok),
        "preflight_warnings": sum(int(c.get("preflight_warnings") or 0) for c in ok),
        "within_repeats_total": sum(int(c.get("within_repeats") or 0) for c in ok),
        "repeated_sentences_cross": (run.get("cross") or {}).get("repeated_sentences", 0),
        "repeated_phrases_cross": (run.get("cross") or {}).get("repeated_phrases", 0),
        "facts_extracted": (run.get("bible") or {}).get("facts", 0),
        "tokens_per_chapter": round(total_tokens / n_ok) if n_ok and total_tokens else None,
        "seconds_per_chapter": round(sum(c.get("seconds") or 0 for c in ok) / n_ok, 1) if n_ok else None,
    }


# =============== 主流程 ===============
async def run_fixture(
    fx: Fixture,
    *,
    chapters: int | None = None,
    label: str = "run",
    plan: ModelPlan | None = None,
    progress: Callable[[str], None] | None = None,
    verbose_stages: bool = False,
) -> dict[str, Any]:
    """灌夹具 → 逐章生成 → 收数字。返回 run dict(含 `_chapter_texts`,save_run 会拆到旁文件)。"""
    say = progress or (lambda _s: None)
    Base.metadata.create_all(bind=engine)
    n_total = min(chapters or fx.chapter_count, fx.chapter_count)
    started = datetime.now(timezone.utc)
    t_run = time.time()

    with SessionLocal() as db:
        usage_before = db.query(func.max(LlmUsage.id)).scalar() or 0
        project = seed_fixture(db, fx)
        pid = project.id
        settings = {
            "review_pass_threshold": project.review_pass_threshold,
            "review_auto_revise": project.review_auto_revise,
            "review_max_revisions": project.review_max_revisions,
            "target_words": fx.target_words,
        }
    say(f"夹具《{fx.title}》已灌入(project_id={pid}),开始逐章生成 {n_total} 章")

    records: list[dict[str, Any]] = []
    with (plan.applied() if plan else nullcontext()):
        for n in range(1, n_total + 1):
            t0 = time.time()
            title = str(fx.outlines[n - 1].get("title") or "")
            say(f"第 {n} 章《{title}》生成中…")
            stage_cb = (lambda s, _n=n: say(f"  [第 {_n} 章] {s}")) if verbose_stages else None
            try:
                with SessionLocal() as db:
                    project = db.get(Project, pid)
                    result = await generate_chapter(db, project, n, progress=stage_cb)
                    db.commit()
                    rec = chapter_record(n, title, result, fx.target_words, time.time() - t0)
            except Exception as exc:  # noqa: BLE001 — 单章失败记录后继续,别让一章毁一轮
                rec = {
                    "n": n,
                    "title": title,
                    "ok": False,
                    "seconds": round(time.time() - t0, 1),
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
                say(f"第 {n} 章失败:{rec['error']}")
            records.append(rec)
            if rec.get("ok"):
                say(
                    f"第 {n} 章完成:{rec['chars']} 字,主审 {rec['review']['scores']},"
                    f"AI 味 {rec['flavor']['score']},门禁 {rec['gate']},状态 {rec['status']}"
                )

    with SessionLocal() as db:
        rows = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid)
            .order_by(Chapter.chapter_number)
            .all()
        )
        texts = {c.chapter_number: c.final_content or "" for c in rows}
        bible = {
            "entities": db.query(Entity).filter_by(project_id=pid).count(),
            "facts": db.query(Fact).filter_by(project_id=pid).count(),
            "foreshadowings": db.query(Foreshadowing).filter_by(project_id=pid).count(),
        }
        usage_rows = db.query(LlmUsage).filter(LlmUsage.id > usage_before).all()
        usage = {
            "calls": len(usage_rows),
            "prompt_tokens": sum(int(r.prompt_tokens or 0) for r in usage_rows),
            "completion_tokens": sum(int(r.completion_tokens or 0) for r in usage_rows),
        }

    manifest = prompt_manifest()
    run: dict[str, Any] = {
        "schema": RUN_SCHEMA,
        "label": label,
        "fixture": fx.name,
        "fixture_title": fx.title,
        "started_at": started.isoformat(timespec="seconds"),
        "seconds": round(time.time() - t_run, 1),
        "git_commit": git_commit(),
        "models": describe_models(plan),
        "project_id": pid,
        "project_settings": settings,
        "prompt_fingerprint": manifest_fingerprint(manifest),
        "prompt_manifest": manifest,
        "chapters": records,
        "cross": cross_chapter_metrics([texts[k] for k in sorted(texts)]),
        "bible": bible,
        "usage": usage,
    }
    run["aggregate"] = aggregate(run)
    run["_chapter_texts"] = texts
    return run


def save_run(run: dict[str, Any], out_dir: str | Path) -> tuple[Path, Path | None]:
    """run → `<label>-<fixture>-<时间>.json`;正文另存同名 `.chapters.md` 供肉眼读。"""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{run.get('label', 'run')}-{run.get('fixture', 'fixture')}-{stamp}"
    texts = run.pop("_chapter_texts", None) or {}
    text_path: Path | None = None
    if texts:
        text_path = out / f"{base}.chapters.md"
        titles = {c["n"]: c.get("title", "") for c in run.get("chapters") or []}
        parts = [f"# {run.get('fixture_title') or run.get('fixture')} · `{run.get('label')}`\n"]
        for n in sorted(texts):
            parts.append(f"\n## 第 {n} 章 {titles.get(n, '')}\n\n{texts[n].strip()}\n")
        text_path.write_text("".join(parts), encoding="utf-8")
        run["chapters_text_path"] = text_path.name
    json_path = out / f"{base}.json"
    json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, text_path


def load_run(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
