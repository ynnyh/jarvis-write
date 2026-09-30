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
    "series": [APP / "engines" / "series", APP / "api" / "series.py"],
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
    offenders = _hits(_py_files([MEDIA]), _line_import_pattern(["drama", "promo", "clips", "birthday", "series"]))
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


# =============== ⑤ media 共用件与 SRT/CSV 同级 ===============

def test_media_shared_kernels_only_in_media():
    """四条共用件和 SRT、CSV 一样只许有一份。

    今天没出现重复实现,但那是「人还记得」而不是「机器拦着」:切段的时间码、
    画风锚兜底、音频分轨、画风方向目录,四条线各自写一遍的形状和当初 SRT/CSV
    一模一样,而这类口径分叉只在用户手上暴露(导出对不上)。
    """
    rules = [
        (r"def (plan_chunks|chunk_rows|group_by_limit)\b", "segments.py"),
        (r"def ensure_style_anchors\b", "anchors.py"),
        (r"def ensure_audio_rules\b|def audio_track_note\b", "audio.py"),
        (r"VALID_DIRECTIONS\s*=", "directions.py"),
        (r"^BASE\s*:\s*tuple|def ensure_base\b", "negative.py"),
    ]
    files = _py_files([p for ts in LINES.values() for p in ts])
    offenders: list[str] = []
    for rx, kernel in rules:
        for hit in _hits(files, rx):
            offenders.append("%s\n    应改用 app/engines/media/%s" % (hit, kernel))
    assert not offenders, (
        "出片线重写了 media 共用件(共用件只许长在 media/):\n" + "\n".join(offenders))


# =============== ⑥ 视频提示词不许写死字数下限 ===============

# 只扫「视频提示词类」模板。正文/剧本/角色定妆不在判据范围内——那里
# 「越详细越好」是对的(定妆 appearance 少于 100 字正是 anime 出片崩脸的原因之一)。
# 限定扫描范围而不是列豁免:判据自洽、可解释,不需要维护例外名单。
VIDEO_PROMPT_TEMPLATES = {
    "SEGMENTED_FILM_PROMPT_TEMPLATE",
    "WHOLE_CLIP_PROMPT_TEMPLATE",
    "ANIME_SEGMENT_PROMPT_TEMPLATE",
    "ANIME_SHOTCARD_PROMPT",
}


def test_video_prompt_templates_have_no_length_floor():
    """视频提示词只许「封顶 + 必填清单」,不许「只设下限、上不封顶」。

    为什么:视频模型对长提示词是**抽样执行**——写���越满丢得越多,凑字数只会
    稀释有效指令(docs/24 裁定 + docs/25 §3.2)。而这条旧口径对文本模型成立,
    两条线混用同一个「不许压缩」就等于给视频模型下错指令。

    整改方向是 `{length_rule}` 之类的占位:口径搬进 Skill 包,由包控开关二选一。
    """
    files = _py_files([APP / "prompts"])
    offenders: list[str] = []
    for f in files:
        text = _read(f)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*\"\"\"", text, re.M):
            name = m.group(1)
            if name not in VIDEO_PROMPT_TEMPLATES:
                continue
            body = text[m.end():].split('\"\"\"')[0]
            bad = [ln.strip() for ln in body.splitlines()
                   if re.search(r"上不封顶|不许压缩|不少于\s*\{", ln)]
            if bad:
                rel = f.relative_to(APP.parent)
                offenders.append("%s :: %s\n    %s" % (rel, name, bad[0][:80]))
    assert not offenders, (
        "视频提示词模板里写死了「只设下限/上不封顶」:\n" + "\n".join(offenders))


# =============== ⑦ 模板声明了槽,引擎必须真传 ===============

def _format_call(text: str, start: int) -> str:
    """从 `.format(` 的左括号起做**括号配平**,返回整个调用原文。

    为什么不能按固定字符窗口截:真实调用有二十来个 kwargs,skill_block 常常
    排在很靠后——按窗口扫会把「已经传了」误报成「没传」,门禁一有假阳性就会
    被加豁免,那等于门禁作废。
    """
    i = text.index("(", start) + 1
    depth = 1
    while i < len(text) and depth:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    return text[start:i]


def test_skill_slot_is_actually_filled():
    """防「槽加了没人填」——最隐蔽的失效:模板安静地留着 {skill_block}。

    走 formOnly 会当场 KeyError(所以不会静默);真正的风险是反过来的方向:
    有人在模板里加了槽、引擎没接,于是这条工序**永远拿不到工艺包**。
    本判据盯的是「引擎侧调 .format 时有没有把 skill_block 传进去」。
    """
    tpl_slots: dict[str, str] = {}
    for f in _py_files([APP / "prompts"]):
        text = _read(f)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*\"\"\"(.*?)\"\"\"", text, re.S | re.M):
            if "{skill_block}" in m.group(2):
                tpl_slots[m.group(1)] = str(f)

    unfilled: list[str] = []
    for f in _py_files([APP / "engines"]) + _py_files([APP / "api"]):
        text = _read(f)
        for m in re.finditer(r"([A-Z][A-Z0-9_]*)\.format\(", text):
            if m.group(1) in tpl_slots and "skill_block=" not in _format_call(text, m.start()):
                unfilled.append("%s :: %s" % (f.relative_to(APP.parent), m.group(1)))
    assert not unfilled, (
        "模板声明了 {skill_block} 槽但引擎没传(该工序将永远拿不到 Skill 包):\n"
        + "\n".join(sorted(set(unfilled))))
