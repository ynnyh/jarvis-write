# tests/test_engine_conventions.py
# -*- coding: utf-8 -*-
"""引擎分层公约门禁:扫源码,挡住三条出片线「各写一份」与依赖方向反了的复发。

前端有 `uiConventions.test.ts` 扫版面公约,后端这一条对应的是引擎分层:
漫剧 / 宣传片 / 情绪短片三条线共用的确定性件只许长在 `app/engines/media/` 里。

为什么要门禁而不是靠自觉:这三条线的形状太像,新起一条线时最省事的写法就是
`from app.engines.drama.xxx import ...`(实际发生过:宣传片与情绪短片都曾跨模块
拿 `drama.exporter._srt_blocks` 这个**私有**名),或者干脆把 SRT 时间码、CSV 的
BOM、脏值收敛再抄一遍。抄一遍就等于口径分叉:改了一处忘另一处,导出的字幕对不上、
Excel 打开是乱码,而这类 bug 只在用户手上暴露。

四条判据都**没有豁免清单**——被拦住时只有两条出路:把件挪进 `media/`,
或者证明判据本身写错了。加豁免名单等于给复发开门。
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# 出片线各自的目录 + 接口层(drama 接口层已拆成包,递归扫)
LINES = {
    "drama": [APP / "engines" / "drama", APP / "api" / "drama"],
    "promo": [APP / "engines" / "promo", APP / "api" / "promo.py"],
    "clips": [APP / "engines" / "clips", APP / "api" / "clips.py"],
    "birthday": [APP / "engines" / "birthday", APP / "api" / "birthday.py"],
}
MEDIA = APP / "engines" / "media"


def _py_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        out.extend(sorted(t.rglob("*.py")) if t.is_dir() else [t])
    return out


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _hits(files: list[Path], pattern: str) -> list[str]:
    """返回 "相对路径:行号: 行内容" 列表(报错信息要能直接跳到现场)。"""
    rx = re.compile(pattern)
    out: list[str] = []
    for f in files:
        for i, line in enumerate(_read(f).splitlines(), 1):
            if rx.search(line):
                out.append(f"{f.relative_to(APP.parent)}:{i}: {line.strip()}")
    return out


def _line_import_pattern(names: list[str]) -> str:
    """匹配把 `names` 里的线 import 进来的三种写法。

    第三种 `from app.engines import drama` 最容易漏:它不带点号路径,
    只匹配 `from app.engines.<line>` 的正则对它完全失明。
    """
    alt = "|".join(names)
    return (
        rf"from app\.engines\.({alt})\b"
        rf"|import app\.engines\.({alt})\b"
        rf"|from app\.engines import [^\n]*\b({alt})\b"
    )


# =============== ① 出片线之间不许互相 import ===============

def test_lines_do_not_import_each_other():
    """宣传片/情绪短片不是漫剧的下游,反过来也不是。共用件走 media,别互相转引。"""
    offenders: list[str] = []
    for name, targets in LINES.items():
        others = [n for n in LINES if n != name]
        offenders += _hits(_py_files(targets), _line_import_pattern(others))
    assert not offenders, (
        "出片线之间互相 import 了(共用的确定性件请挪进 app/engines/media/,"
        "各线自己的业务口径不要跨线复用):\n" + "\n".join(offenders)
    )


# =============== ② media 是叶子:不许反向依赖任何一条线 ===============

def test_media_does_not_depend_on_any_line():
    offenders = _hits(_py_files([MEDIA]), _line_import_pattern(["drama", "promo", "clips", "birthday"]))
    assert not offenders, (
        "app/engines/media/ 反向依赖了某条出片线——它必须是叶子(只含三线共用的"
        "确定性口径,不含任何一条线的业务):\n" + "\n".join(offenders)
    )


# =============== ③ SRT 内核只此一份 ===============

def test_srt_kernel_only_in_media():
    """时间码格式化与 "-->" 拼装只许出现在 media/subtitles.py。

    时间轴口径分叉 = 导出的字幕和剪辑清单对不上,这种 bug 只在用户手上暴露。
    """
    files = _py_files([p for ts in LINES.values() for p in ts])
    offenders = _hits(files, r"-->|3600_000|3600000")
    assert not offenders, (
        "出片线里自己拼 SRT 了,请改用 app/engines/media/subtitles.py 的 "
        "srt_blocks / srt_from_rows:\n" + "\n".join(offenders)
    )


# =============== ④ CSV 的 BOM 只此一份 ===============

def test_csv_writer_only_in_media():
    """csv.writer 与 UTF-8 BOM 只许出现在 media/text.py 的 csv_text 里。

    少一个 BOM,Excel 打开就是一张中文乱码表——三条线各写一遍,必然漏。
    """
    files = _py_files([p for ts in LINES.values() for p in ts])
    offenders = _hits(files, r"csv\.writer|\\ufeff|﻿")
    assert not offenders, (
        "出片线里自己拼 CSV 了,请改用 app/engines/media/text.py 的 csv_text"
        "(它负责 BOM):\n" + "\n".join(offenders)
    )
