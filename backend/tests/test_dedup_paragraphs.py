# tests/test_dedup_paragraphs.py
# -*- coding: utf-8 -*-
"""章内重复段落去重(治模型"复读"bug)dedup_paragraphs 单元测试。

覆盖:逐字重复(相邻/全章)删除、近似重复(相邻窗口内)删除、短句/对白/refrain 豁免、
相似但不同段保留、远处近似不误删(窗口约束)、空白无关判重、删段后不留三连空行、
无重复原样返回。保守优先——宁漏勿误伤有意呼应。
"""
from __future__ import annotations

from app.engines.consistency.repetition import dedup_paragraphs

# 三个彼此不同、均超过 30 字门槛的"实质段落"
A = "他推开吱呀作响的木门走进屋子,屋里漆黑一片,只有窗外漏进来的月光落在斑驳的地板上,泛着冷冷的青白。"
B = "她站在原地没有动,手指紧紧攥着裙角,心跳快得像要从胸口里蹦出来,却一个字也说不出口。"
C = "远处传来几声犬吠,又很快归于沉寂,夜色浓得化不开,时间仿佛在这一刻彻底停住了。"


def test_exact_adjacent_duplicate_removed():
    """相邻整段逐字重复:删后一段,保留首段。"""
    cleaned, n = dedup_paragraphs(f"{A}\n\n{A}\n\n{B}")
    assert n == 1
    assert cleaned == f"{A}\n\n{B}"


def test_exact_duplicate_global_across_paragraphs():
    """非相邻的逐字重复也按全章去重(复读段落可能出现在很后面)。"""
    cleaned, n = dedup_paragraphs(f"{A}\n\n{B}\n\n{C}\n\n{A}")
    assert n == 1
    assert cleaned == f"{A}\n\n{B}\n\n{C}"


def test_near_duplicate_adjacent_removed():
    """相邻近似段(仅差一两字)也删——最典型的复读变体。"""
    a2 = A.replace("漆黑一片", "漆黑一团")  # 仅 1 字之差,相似度远超阈值
    assert a2 != A
    cleaned, n = dedup_paragraphs(f"{A}\n\n{a2}\n\n{B}")
    assert n == 1
    assert cleaned == f"{A}\n\n{B}"


def test_short_lines_and_refrain_exempt():
    """短句/对白/刻意 refrain(< 30 字)一律豁免,不判重。"""
    text = "「走!」\n\n「走!」\n\n「不。」\n\n「不。」"
    cleaned, n = dedup_paragraphs(text)
    assert n == 0
    assert cleaned == text
    # 稍长但仍 < 30 字的呼应句也保留
    refrain = "这一切都会过去的。"
    text2 = f"{refrain}\n\n{A}\n\n{refrain}"
    cleaned2, n2 = dedup_paragraphs(text2)
    assert n2 == 0
    assert cleaned2 == text2


def test_similar_but_distinct_paragraphs_kept():
    """共享开头但内容明显不同的两段:相似度不到阈值,都保留(不误伤)。"""
    d1 = "他沿着长长的走廊一直往前走,两边的房间门都紧闭着,尽头是一扇雕花的旧木门。"
    d2 = "他沿着长长的走廊一直往前走,忽然听见身后传来一阵急促的脚步声,由远及近。"
    cleaned, n = dedup_paragraphs(f"{d1}\n\n{d2}")
    assert n == 0
    assert cleaned == f"{d1}\n\n{d2}"


def test_distant_near_duplicate_not_removed():
    """近似重复只在相邻窗口内判——隔了 >3 段的近似段不删(保护远处有意呼应)。"""
    a2 = A.replace("漆黑一片", "漆黑一团")
    e1 = "清晨的菜市场已经热闹起来,摊贩的吆喝声此起彼伏,空气里混着鱼腥和青菜的味道。"
    e2 = "他在银行排了整整一个上午的队,好不容易轮到自己,窗口却挂出了暂停服务的牌子。"
    e3 = "山路蜿蜒向上,越往高处走雾气越浓,脚下的碎石松动,每一步都得格外小心。"
    text = f"{A}\n\n{e1}\n\n{e2}\n\n{e3}\n\n{a2}"
    cleaned, n = dedup_paragraphs(text)
    assert n == 0
    assert cleaned == text


def test_whitespace_insensitive_key():
    """判重键剥空白:仅多出全角空格/换行的重复段也能识别。"""
    a_ws = A + "　"  # 尾随一个全角空格
    cleaned, n = dedup_paragraphs(f"{A}\n\n{a_ws}\n\n{B}")
    assert n == 1
    assert cleaned == f"{A}\n\n{B}"


def test_no_triple_newline_after_removal():
    """删段后不得残留三连以上换行,段落结构保持整洁。"""
    cleaned, n = dedup_paragraphs(f"{A}\n\n{A}\n\n{B}\n\n{C}")
    assert n == 1
    assert "\n\n\n" not in cleaned
    assert cleaned == f"{A}\n\n{B}\n\n{C}"


def test_no_duplicate_returns_original_object():
    """无重复:原样返回(同一对象)+ 计数 0,零改动零开销。"""
    text = f"{A}\n\n{B}\n\n{C}"
    cleaned, n = dedup_paragraphs(text)
    assert n == 0
    assert cleaned is text


def test_empty_and_blank_input():
    """空/纯空白输入不炸,原样返回。"""
    assert dedup_paragraphs("") == ("", 0)
    blank = "   \n\n  "
    assert dedup_paragraphs(blank) == (blank, 0)


def test_triple_repeat_removes_two():
    """同段连出三遍:删掉后两遍,只留一遍。"""
    cleaned, n = dedup_paragraphs(f"{A}\n\n{A}\n\n{A}\n\n{B}")
    assert n == 2
    assert cleaned == f"{A}\n\n{B}"
