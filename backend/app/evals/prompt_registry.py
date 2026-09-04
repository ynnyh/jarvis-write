# app/evals/prompt_registry.py
# -*- coding: utf-8 -*-
"""Prompt 指纹清单:给每条 prompt 模板算稳定内容哈希,评测结果据此知道「哪些 prompt 变了」。

为什么用内容哈希而不是手写版本号:版本号靠人记得去加,漏加一次就会出现
「两次评测数字不同、清单却说 prompt 没变」的假象;内容哈希零维护、不会撒谎。

覆盖范围:
- app/prompts/*.py 里所有模块级大写常量(str 常量如 CHAPTER_DRAFT_PROMPT,
  以及胶囊类 dict/list 常量——胶囊正文也是 prompt 内容,改了同样要被看见);
- app/prompts/templates/**/*.txt 文件模板;
- 少数历史原因写在引擎/接口模块里、以 _PROMPT 结尾的常量(见 _EXTRA_MODULES)。

哈希前统一换行符:同一份 .txt 在 Windows/Linux 检出的 CRLF/LF 差异不该算「prompt 变了」。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import pkgutil
import re
from pathlib import Path

import app.prompts as _prompts_pkg

logger = logging.getLogger("jarvis-write.evals")

_TEMPLATES_DIR = Path(_prompts_pkg.__file__).resolve().parent / "templates"
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# 短于此的大写 str 常量(分隔符、枚举值)不算 prompt
_MIN_STR_LEN = 40
_SKIP_MODULES = {"loader", "__init__"}
# 没放进 app/prompts 的 prompt 常量:只收名字以 _PROMPT 结尾的,避免把接口模块里的
# 路由表/枚举也当成 prompt
_EXTRA_MODULES = (
    "app.engines.drift.self_heal",
    "app.api.projects.naming",
    "app.api.tendency",
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _stable_text(value) -> str | None:
    """常量值 → 可哈希文本;不是 prompt 形态的值返回 None。"""
    if isinstance(value, str):
        return _normalize(value) if len(value) >= _MIN_STR_LEN else None
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return None
    return None


def _module_prompts(mod, prefix: str, *, only_prompt_suffix: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in vars(mod).items():
        if not _NAME_RE.match(name):
            continue
        if only_prompt_suffix and not name.endswith("_PROMPT"):
            continue
        text = _stable_text(value)
        if text is None:
            continue
        out[f"{prefix}.{name}"] = text
    return out


def collect_prompts() -> dict[str, str]:
    """收集全部 prompt 文本:{名字: 正文}。

    名字形如 `chapter.CHAPTER_DRAFT_PROMPT` / `templates/editorial/review.txt` /
    `engines.drift.self_heal.XXX_PROMPT`。
    """
    found: dict[str, str] = {}
    for info in pkgutil.iter_modules(_prompts_pkg.__path__):
        if info.name in _SKIP_MODULES or info.ispkg:
            continue
        mod = importlib.import_module(f"app.prompts.{info.name}")
        found.update(_module_prompts(mod, info.name, only_prompt_suffix=False))
    for dotted in _EXTRA_MODULES:
        try:
            mod = importlib.import_module(dotted)
        except Exception as exc:  # noqa: BLE001 — 某个模块导入失败不该让整份清单失效
            logger.warning("prompt 清单跳过模块 %s: %s", dotted, exc)
            continue
        found.update(
            _module_prompts(mod, dotted.removeprefix("app."), only_prompt_suffix=True)
        )
    if _TEMPLATES_DIR.is_dir():
        for path in sorted(_TEMPLATES_DIR.rglob("*.txt")):
            rel = path.relative_to(_TEMPLATES_DIR).as_posix()
            found[f"templates/{rel}"] = _normalize(path.read_text(encoding="utf-8"))
    return found


def prompt_manifest() -> dict[str, str]:
    """{名字: 12 位内容哈希},按名字排序。"""
    return {name: _digest(text) for name, text in sorted(collect_prompts().items())}


def manifest_fingerprint(manifest: dict[str, str]) -> str:
    """整份清单的一个总指纹:任何一条 prompt 变了它就变。"""
    return _digest(json.dumps(manifest, sort_keys=True))


def diff_manifests(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    """两份清单的差异:新增 / 删除 / 内容变了。"""
    old_keys, new_keys = set(old), set(new)
    return {
        "added": sorted(new_keys - old_keys),
        "removed": sorted(old_keys - new_keys),
        "changed": sorted(k for k in old_keys & new_keys if old[k] != new[k]),
    }
