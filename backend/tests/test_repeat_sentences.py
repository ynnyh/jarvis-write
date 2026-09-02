# tests/test_repeat_sentences.py
"""句子级跨章查重(治'同一句话反复复读')单测。

覆盖:跨章同句命中 → avoid_block 出【禁止逐字复用】清单、单章只计一次、
< _MIN_SENT_CHARS 的碎句/对白豁免、全书干净返回空串。
"""
from app.engines.consistency.repetition import (
    avoid_block,
    find_repeated_sentences,
    _MIN_SENT_CHARS,
)


def test_cross_chapter_repeated_sentence_flagged():
    """那句'流水触感'在近两章各出现一次 → 跨章查重命中。"""
    texts = [
        "他掬水洗脸,水流冲过他的手指,凉凉的,有点麻。\n他抬头看天。",
        "她把水撩起来,水流冲过他的手指,凉凉的,有点麻。\n她笑了笑。",
    ]
    hits = find_repeated_sentences(texts)
    assert any("水流冲过他的手指" in s for s, _ in hits)
    block = avoid_block(texts)
    assert "禁止逐字复用" in block
    assert "水流冲过他的手指" in block


def test_single_occurrence_not_flagged():
    """同一句只在近几章出现一次 → 不命中,avoid_block 干净(无句子级)。"""
    texts = [
        "水流冲过他的手指,凉凉的,有点麻。\n他抬头看天。",
        "她撩起水花,水珠顺着指缝滑落。\n她笑了笑。",
    ]
    hits = find_repeated_sentences(texts)
    assert hits == []


def test_same_chapter_repeat_counts_once():
    """同一章内复读不算跨章:只计章节间出现的次数,章内多次不重复加权。"""
    texts = [
        "水流冲过他的手指,凉凉的,有点麻。\n水流冲过他的手指,凉凉的,有点麻。",
        "她把水撩起来,水珠顺着指缝滑落。\n她笑了笑。",
    ]
    hits = find_repeated_sentences(texts)
    assert hits == []


def test_short_fragment_exempt():
    """不足 _MIN_SENT_CHARS 的碎句/对白一律豁免,不误伤有意复沓。"""
    short = "嗯"
    assert len(short) < _MIN_SENT_CHARS
    texts = [f"{short}。\n他应了一声。", f"{short}。\n她应了一声。"]
    hits = find_repeated_sentences(texts)
    assert hits == []


def test_empty_inputs():
    """空输入/全书干净 → 空串,零开销。"""
    assert find_repeated_sentences([]) == []
    assert avoid_block([]) == ""