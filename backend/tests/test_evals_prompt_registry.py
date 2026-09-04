# tests/test_evals_prompt_registry.py
# -*- coding: utf-8 -*-
"""评测底座·prompt 指纹:内容稳定、改动敏感、diff 分类正确。

不验证 21 个 prompt 模板的具体内容(那等于复制源码)——只验证
- 同一份源码指纹两次跑一致;
- 在 prompts/ 下加 1 字立刻变;
- diff_manifests 把变更分类到 changed/added/removed 三档。
"""
from __future__ import annotations

from app.evals.prompt_registry import (
    collect_prompts,
    diff_manifests,
    manifest_fingerprint,
    prompt_manifest,
)


def _prompt_texts() -> dict[str, str]:
    return collect_prompts()


def test_prompt_manifest_is_deterministic_across_runs():
    a = prompt_manifest()
    b = prompt_manifest()
    assert a == b
    assert a  # 非空(项目里 21 个 prompts 模块都有内容)
    # 每条指纹 12 位 hex
    assert all(len(v) == 12 and all(c in "0123456789abcdef" for c in v) for v in a.values())


def test_prompt_fingerprint_changes_when_one_byte_changes():
    manifest = prompt_manifest()
    fp_before = manifest_fingerprint(manifest)
    # 改一条 prompt 的最后 1 字——必须变
    name = next(iter(manifest))
    # _stable_text 直接篡改字典值模拟「改动源码」,不走文件系统
    import app.evals.prompt_registry as pr

    texts = _prompt_texts()
    texts[name] = texts[name] + "改"
    fake_manifest = {n: pr._digest(t) for n, t in texts.items()}
    fp_after = manifest_fingerprint(fake_manifest)
    assert fp_before != fp_after, "改了 1 字应该得到不同总指纹"


def test_diff_manifests_classifies_three_kinds():
    manifest = prompt_manifest()  # 真实清单
    fake = dict(manifest)
    # 改一条:内容变了 key 还在
    first_key = next(iter(fake))
    fake[first_key] = "0" * 12
    # 加一条
    fake["new.added.prompt"] = "b" * 12
    # 删一条(挑一个不是刚刚被改过的)
    other_key = next(k for k in manifest if k != first_key)
    fake.pop(other_key)
    diff = diff_manifests(manifest, fake)
    assert first_key in diff["changed"]
    assert "new.added.prompt" in diff["added"]
    assert other_key in diff["removed"]
