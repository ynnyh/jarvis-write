# tests/test_media_markdown.py
# -*- coding: utf-8 -*-
"""出片手册的 Markdown 装配内核(三条线共用,口径见 media/markdown.py)。

为什么值得一个文件:这一层不产出业务内容,只决定**手册长什么样**——而「长什么样」
正是用户拿到手的东西。四个 exporter 各写一遍时,同一个表格转义出现了两种做法:
宣传片/漫剧把单元格里的换行压平了,情绪短片/生日祝福没有,于是同一句带换行的
台词,在手卡里会把整张表拆成两半(表头 7 列、数据行只剩 6 列,渲染出来是散的)。
这里钉四件事:

① 表格单元格:竖线转义成 `/`、换行压平、`None` 出空串(不是 "None");
② 小节空行:标题后该不该留空行由调用方声明(`blank=`),不靠每个 exporter 各记一遍;
③ 缺值口径:三轨提示词缺轨道写「(未生成)」而不是留空;
④ 端到端:带换行的台词进分镜表之后,表格仍然是完整的(这次统一修掉的那个 bug)。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.engines.clips.exporter import export_markdown as clips_md
from app.engines.media.markdown import Md, cell, table_rows


# =============== 单元格与表格 ===============

def test_cell_escapes_pipe_and_flattens_newline():
    """竖线会拆列、换行会断行——两个都必须先清掉再进表格。"""
    assert cell("她低头|抿唇") == "她低头/抿唇"
    assert cell("别走\n行吗") == "别走 行吗"
    assert cell("别走\r\n行吗") == "别走 行吗"  # \r 留着会在 Windows 上多一个断行
    assert cell("别走\r行吗") == "别走 行吗"


def test_cell_keeps_zero_and_nulls_to_empty():
    """0 是有效值(第 0 格/0 秒),不能当空值吃掉;None 才出空串。"""
    assert cell(0) == "0"
    assert cell(None) == ""
    assert cell(False) == "False"


def test_table_rows_shape():
    """表头 + 分隔行 + 数据行;分隔行列数必须等于表头列数,否则 GFM 不认表。"""
    lines = table_rows(["#", "台词"], [[1, "别走"], [2, None]])
    assert lines[0] == "| # | 台词 |"
    assert lines[1] == "|---|---|"
    assert lines[2] == "| 1 | 别走 |"
    assert lines[3] == "| 2 |  |"


def test_md_table_blank_follows_flag():
    """表格后的空行由调用方决定:宣传片的简报表后面紧跟条件分支,自己会补空行。"""
    assert Md().table(["a"], [[1]]).lines[-1] == ""
    assert Md().table(["a"], [[1]], blank=False).lines[-1] == "| 1 |"


# =============== 小节与空行 ===============

def test_heading_blank_is_explicit():
    """h2 默认留空行(多数用法);h3 默认不留(下面直接跟正文块)。"""
    assert Md().h2("分镜").lines == ["## 分镜", ""]
    assert Md().h2("角色卡(本集出场)", blank=False).lines == ["## 角色卡(本集出场)"]
    assert Md().h3("镜头 1(近景/推近/4s)").lines == ["### 镜头 1(近景/推近/4s)"]
    assert Md().h1("手卡").lines == ["# 手卡", ""]


# =============== 三轨提示词块 ===============

def test_shot_tracks_fills_missing_track_labels():
    """缺轨道写「(未生成)」:留空分不清「没生成」和「生成失败」。"""
    lines = Md().shot_tracks(
        seq=1, shot_type="近景", camera="推近", duration_s=4,
        prompt_cn="", prompt_en="", negative="",
    ).lines
    assert lines[0] == "### 镜头 1(近景/推近/4s)"
    assert "(未生成)" in lines
    assert lines[-2] == "**负面**:(无)"
    assert lines[-1] == ""


def test_shot_tracks_labels_are_injected_by_caller():
    """标签是文案不是口径:宣传片/漫剧传自己的长标签,默认那套给情绪短片与生日祝福。"""
    lines = Md().shot_tracks(
        seq=2, shot_type="远景", camera="固定", duration_s=5,
        prompt_cn="雪原", prompt_en="snow", negative="多手多指",
        cn_label="**中文提示词(即梦/可灵)**",
        en_label="**英文提示词(Midjourney)**",
        neg_label="**负面提示词**",
    ).lines
    assert "**中文提示词(即梦/可灵)**" in lines
    assert "**英文提示词(Midjourney)**" in lines
    assert "**负面提示词**:多手多指" in lines


# =============== 清单 ===============

def test_speech_skips_non_dict_and_can_number():
    lines = Md().speech([{"speaker": "她", "text": "别走"}, "脏数据"],
                        numbered=True).lines
    assert lines == ["1. **她**:别走", ""]
    assert Md().speech([{"speaker": "他", "text": "算了"}], blank=False).lines == [
        "- **他**:算了"
    ]


def test_segments_marks_over_limit_and_keeps_timeline():
    lines = Md().segments([
        {"index": 1, "start_s": 0, "end_s": 18, "shot_seqs": [1, 2], "over_limit": True},
    ]).lines
    assert lines[0] == "## 生成切段(一段一次生成,画布拼接)"
    assert lines[1] == ""
    assert lines[2] == "- **段 1**(0-18s,镜头 1、2) ⚠超限"


def test_style_anchor_order_is_fixed():
    """三行顺序固定(中文 / 英文 / 负面基座):手册是照着干活的,位置不能随实现漂。"""
    lines = Md().style_anchor("国风厚涂", "ink-wash", "多手多指").lines
    assert lines == [
        "## 画风锚", "",
        "- 国风厚涂",
        "- EN: ink-wash",
        "- 负面基座:多手多指",
        "",
    ]


# =============== 端到端:这次统一修掉的那处 ===============

def _clip_row(dialogue: str) -> SimpleNamespace:
    shots = [
        {"seq": 1, "duration_s": 5, "scene_name": "巷口", "shot_type": "近景",
         "camera": "固定", "action_desc": "她低头", "dialogue": dialogue,
         "prompt_cn": "巷口近景", "prompt_en": "alley", "negative": "多手多指"}
    ]
    return SimpleNamespace(
        clip={"take": "A 版", "shots": shots, "chunks": [], "lines": [], "punchline": ""},
        theme="regret", custom_theme="", duration_s=15, direction="anime",
        style_name="国风厚涂", style_cn="国风厚涂", style_en="ink-wash", negative="多手多指",
    )


def test_multiline_dialogue_does_not_break_the_shot_table():
    """带换行的台词以前会把分镜表拆成两半(宣传片/漫剧压平了,短片/生日没压)。

    这是这次抽公共内核顺手修掉的一处:两种做法并存时,同一句台词在四条线里
    有两种渲染结果。钉法:表格数据行必须只有一行,且列数与表头一致。
    """
    md = clips_md(_clip_row("别走\n行吗"))
    rows = [l for l in md.splitlines() if l.startswith("|")]
    header, sep, *data = rows  # 分隔行以 "|---" 开头,同属表格行
    assert data, "分镜表没有数据行"
    assert len(header.split("|")) == len(sep.split("|")) == len(data[0].split("|"))
    assert "别走 行吗" in data[0]
    assert "别走\n行吗" not in md
