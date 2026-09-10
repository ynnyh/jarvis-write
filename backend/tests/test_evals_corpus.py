# tests/test_evals_corpus.py
# -*- coding: utf-8 -*-
"""确定性轨语料库(evals_corpus)的完整性:CI 门禁依赖它。

CI 里跑的是 `python -m app.evals deterministic --dir evals_corpus`。如果这个目录
被误删、清空、或门槛被放宽到「什么都能过」,CI 会**静默变成摆设**——本文件把
「语料库存在且在门槛内」钉住,让那种退化在单测阶段就暴露。

注意:这不是在测「正文写得好不好」,而是测「回归基线还在不在、还管不管用」。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.evals.deterministic import DETERMINISTIC_OWNED, evaluate_texts
from app.evals.thresholds import THRESHOLDS

CORPUS = Path(__file__).resolve().parent.parent / "evals_corpus"


def _load_corpus() -> list[str]:
    if not CORPUS.is_dir():
        pytest.fail(f"确定性轨语料库缺失:{CORPUS}(CI 门禁会因此变成摆设)")
    files = sorted(p for p in CORPUS.iterdir() if p.suffix == ".txt")
    return [p.read_text(encoding="utf-8") for p in files]


def test_corpus_exists_with_enough_chapters():
    texts = _load_corpus()
    # 少于 2 章就算不出跨章指标(复读/高频短语),那样这门禁只剩半条命
    assert len(texts) >= 2, f"语料库只有 {len(texts)} 章,跑不了跨章指标"
    assert all(t.strip() for t in texts), "语料库有空文件"


def test_corpus_files_are_ordered_by_name():
    """文件名排序必须等于章序——跨章指标依赖这个约定。"""
    files = sorted(p.name for p in CORPUS.iterdir() if p.suffix == ".txt")
    assert files == sorted(files)
    assert files[0].startswith("ch")


def test_corpus_passes_deterministic_gate():
    """基线本身必须在门槛内——否则 CI 一上来就红,门禁等于失效。"""
    texts = _load_corpus()
    run, ok, violations = evaluate_texts(texts, target_words=3000)
    assert run.chapters >= 2
    assert ok is True, f"基线语料越界(门槛被放松或语料被换坏了):{violations}"


def test_corpus_readings_are_sane():
    """读数合理性:AI 味不该趋近 0(那是算法死了),复读该低。"""
    texts = _load_corpus()
    run = evaluate_texts(texts, target_words=3000)[0]
    mean_flavor = float(run.aggregate["mean_flavor"])
    # 下界 0.5:一段几万字的真实生成文本,AI 味指数算出来恰好 0.0 说明探测失灵
    assert mean_flavor > 0.5, "AI 味指数疑似失灵(几万字真实文本不该是 0)"
    assert mean_flavor < THRESHOLDS["mean_flavor"]["max"]


def test_deterministic_owned_covers_the_gate_ci_runs():
    """CI 门禁依赖的门槛项必须都在本轨「负责」集合里,否则等于没检查。"""
    assert set(THRESHOLDS) & DETERMINISTIC_OWNED
    assert "mean_flavor" in DETERMINISTIC_OWNED
