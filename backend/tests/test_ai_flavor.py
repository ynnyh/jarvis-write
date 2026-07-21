# tests/test_ai_flavor.py
# -*- coding: utf-8 -*-
"""AI 味指数:分类分权重规则库 + 统计指标 + 命中明细 + 接口兼容。"""
from app.api.chapters import _flavor_dict
from app.engines.polish.ai_flavor import ai_flavor_report

# AI 腔样文:神态套话 + 稳妥套话 + 节拍器句(三连等长)+ 逻辑连接癖 + 段尾总结
AI_SAMPLE = (
    "她眼中闪过一丝不易察觉的慌乱,嘴角勾起一抹弧度。空气仿佛凝固了,时间仿佛静止了。"
    "他沉默片刻,微微一笑。他轻声说道,语气平静。他缓缓开口,目光如炬。"
    "首先,我们必须承认这个事实。其次,事情并没有那么简单。最后,一切都已经注定。"
    "一方面他渴望自由,另一方面他恐惧未知。综上所述,命运的齿轮开始转动。\n"
    "他沉默片刻,淡淡地道。他轻声说道,语气平静。他缓缓开口,目光如炬。"
    "总而言之,这个故事告诉我们,勇气终将战胜恐惧。"
)

# 干净样文:句长起伏大、无套话、无段尾总结
CLEAN_SAMPLE = (
    "老张把烟头摁灭在墙上,说了句走吧。巷子里没人。风把门带上了,咣当一声。"
    "他数了数兜里的钱,七块,够一碗面,不够一杯酒。面摊的灯泡晃了一下。"
    "老板娘问他,还加蛋吗。他摇头。"
    "雨下起来了,先是几滴,跟着就是一片,砸在铁皮棚上噼啪作响。"
    "他把衣领竖起来,走进雨里,身后的灯一盏一盏灭了。"
)


def test_ai_sample_scores_much_higher_than_clean():
    r_ai = ai_flavor_report(AI_SAMPLE)
    r_clean = ai_flavor_report(CLEAN_SAMPLE)
    assert r_ai.score > 20
    assert r_ai.score > r_clean.score * 3
    assert r_clean.score == 0
    assert "未检出" in r_clean.summary()
    assert f"{r_ai.score:.1f}" in r_ai.summary()


def test_categories_independent_scores_and_weights():
    r = ai_flavor_report(AI_SAMPLE)
    cats = r.categories
    # 命中的类别各自独立计分
    for name in ("万能神态套话", "稳妥表达癖", "总结过渡腔", "逻辑连接癖", "说教报告腔"):
        assert name in cats, name
        assert cats[name]["count"] >= 1
        assert cats[name]["score"] == round(cats[name]["count"] * cats[name]["weight"], 2)
    # 网文神态套话权重最高
    assert cats["万能神态套话"]["weight"] == max(c["weight"] for c in cats.values())
    # summary 概括最主要问题类别
    assert "万能神态套话" in r.summary()


def test_hits_detail_for_polish_loop():
    r = ai_flavor_report(AI_SAMPLE)
    assert r.hits, "应有命中明细"
    hit = next(h for h in r.hits if h.phrase == "眼中闪过一丝")
    assert hit.category == "万能神态套话"
    assert "眼中闪过一丝" in hit.sentence
    assert hit.start == AI_SAMPLE.index("眼中闪过一丝")
    # 每条命中都带类别+原句+位置
    for h in r.hits:
        assert h.category and h.sentence and h.start >= 0


def test_statistical_metrics():
    r = ai_flavor_report(AI_SAMPLE)
    m = r.metrics
    # 节拍器句组:"他沉默片刻,微微一笑。他轻声说道,语气平静。他缓缓开口,目光如炬。"三连等长
    assert m["metronome_groups"] >= 1
    # 段尾总结句:第二段以"总而言之……"收尾
    assert m["tail_summary_count"] >= 1
    # burstiness 字段存在(句数足够时给出数值)
    assert m["burstiness"] is not None
    assert m["sentence_count"] >= 8


def test_long_repeat_detection():
    seg = "月亮从云后面出来,照亮了整条空无一人的长街,水洼里全是碎掉的光,远处钟楼敲了三下。" \
          "他站在屋檐下,数着自己的呼吸,一下,两下,三下。"
    assert len(seg) >= 50
    text = seg + "他站了一会儿,不知道该往哪走,兜里只剩下一把冰凉的钥匙。" + seg
    r = ai_flavor_report(text)
    assert r.metrics["repeats"], "50 字以上连续重复应被检出"
    assert r.metrics["repeats"][0]["length"] >= 50
    # 无重复的干净样文不应误报
    assert not ai_flavor_report(CLEAN_SAMPLE).metrics["repeats"]


def test_flavor_dict_shape():
    d = _flavor_dict("仿佛一切都在某种意义上注定了。")
    assert set(d) == {"score", "summary", "categories"}
    assert isinstance(d["score"], float)
    assert isinstance(d["summary"], str)
    assert "比喻连接词癖" in d["categories"]


def test_report_to_dict_json_friendly():
    d = ai_flavor_report(AI_SAMPLE).to_dict()
    assert set(d) == {"score", "summary", "total_chars", "categories", "hits", "metrics"}
    assert isinstance(d["hits"], list)
    assert d["hits"][0]["category"]
