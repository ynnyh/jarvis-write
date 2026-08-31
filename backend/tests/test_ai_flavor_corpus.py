# tests/test_ai_flavor_corpus.py
# -*- coding: utf-8 -*-
"""AI 味检测的评测语料门禁(双指标,借鉴 Renwei 评测法)+ 规则扩容回归。

双指标 = 检测召回(AI 味样本必须过门槛)+ 人类保真(干净人类文本不得误伤)。
改 `_RULES`/权重/门槛前先跑本文件:AI 样本分不达标或人类样本被误伤,
说明判据改坏了,不是语料的问题——别调语料迁就规则。

语料为手写极样本(AI 腔典型 vs 人类白描),非真实书籍摘录。
"""
from __future__ import annotations

from app.engines.polish import polisher
from app.engines.polish.ai_flavor import FlavorReport, ai_flavor_report
from app.engines.polish.polisher import DEAI_GATE_SCORE, judges_regress

# ---------- AI 味语料:八类典型 + 欧化中文 ----------

AI_CORPUS = [
    # 万能神态套话 + 空洞比喻
    ("她眼中闪过一丝不易察觉的慌乱,嘴角勾起一抹弧度。空气仿佛凝固了,"
     "时间仿佛静止了。他沉默片刻,微微一笑,缓缓开口,目光如炬。"),
    # 总结过渡 + 说教报告腔
    ("综上所述,本次事件充分说明了团队合作的重要性。总而言之,我们不难发现,"
     "值得注意的是,沟通永远是解决问题的关键。这个故事告诉我们,信任不可或缺。"),
    # 欧化中文(翻译腔)
    ("随着人工智能的不断发展,教育扮演着至关重要的角色。对于学生而言,"
     "这不仅仅是一次考试,而是一场蜕变,标志着人生的重要转折点,未来可期。"),
    # 造句定式 + 情绪标签直喊
    ("他不是不想说,而是不敢说。与其说这是愤怒,不如说是绝望。"
     "她感到无比悲伤,心中五味杂陈。他进行了深入的思考,愈发凸显出内心的复杂。"),
    # 逻辑连接癖 + 节拍器(等长句)
    ("首先,他看向了窗外。其次,他握紧了拳头。最后,他闭上了眼睛。"
     "一方面,他想起了过去。另一方面,他恐惧着未来。与此同时,雨停了。"
     "他站起身。他走到门口。他拉开门。他迈出一步。他抬头看天。"),
    # 空洞比喻堆砌(脑内涟漪/无形的手)
    ("他的脑海里泛起一阵涟漪,仿佛有人在很远的地方呼唤他的名字。"
     "一种无形的力量牵引着他。他似乎想起了什么,却又抓不住。"
     "记忆的潮水涌动,心底掀起波澜,久久无法平息。"),
]

# ---------- 人类白描语料(老舍/汪曾祺式:动作、细节、对话,无套话) ----------

HUMAN_CORPUS = [
    ("老张把烟头摁灭在墙上,说走吧。巷子里没人。风把门带上,咣当一声。"
     "他数了数兜里的钱,七块,够一碗面,不够一杯酒。面摊的灯泡晃了一下。"
     "老板娘问他还加蛋吗,他摇头。"),
    ("腊月里,河面冻得结实。孩子们抽冰尜,鞭子甩得脆响。二丫站在岸上看,"
     "手拢在袖子里。她娘喊她回去吃饭,她不应,直到天黑透了才挪步。"
     "灶膛里的火还温着,贴饼子的香气没散。"),
    ("手术做完是半夜三点。老陈蹲在走廊尽头,一根烟没点,攥着。护士出来喊家属,"
     "他站起来,腿麻,扶了下墙。人没事。他嗯了一声,又蹲回去,这回把烟点上了。"),
    ("邮递员骑到村口就喊:王秀兰,汇款单!他妈从菜地里直起腰,在围裙上擦手,"
     "一路小跑。单子上五百块。她问哪来的,邮递员说儿子寄的,走了。"
     "她站在太阳底下看了半天,又把单子折好,塞进上衣第二个扣子里面。"),
    ("那年冬天雪大,压塌了鸡窝。父亲半夜起来修,手电筒咬在嘴里,钉子含在唇边,"
     "一颗一颗往木头里砸。我在屋里听,砸一下,停一下。天亮时鸡窝立起来了,"
     "他的手指头冻得握不住筷子,喝粥洒了半碗。"),
    ("课间十分钟,操场上的土被踩起来,黄蒙蒙的。李丰把弹珠弹进了下水道,"
     "趴在铁篦子上看半天,起来时眼睛红的。谁也没笑话他。上课铃响,"
     "一群人往楼里跑,他落在最后,又回头看了一眼。"),
]


def test_ai_corpus_all_above_gate():
    """检测召回:AI 味样本必须全部过门槛(过不了 = 规则覆盖出洞)。"""
    for i, sample in enumerate(AI_CORPUS):
        report = ai_flavor_report(sample)
        assert report.score > DEAI_GATE_SCORE, (
            f"AI 样本[{i}] 得分 {report.score} 未过门槛 {DEAI_GATE_SCORE},"
            f"命中类别: {list(report.categories)}"
        )


def test_human_corpus_no_false_positive():
    """人类保真:干净白描不得误伤(误伤 = 规则写得太宽)。"""
    for i, sample in enumerate(HUMAN_CORPUS):
        report = ai_flavor_report(sample)
        assert report.score <= DEAI_GATE_SCORE, (
            f"人类样本[{i}] 被误伤:得分 {report.score},命中: "
            f"{ {k: v['count'] for k, v in report.categories.items()} }"
        )


def test_dialogue_hits_discounted_and_flagged():
    """对白保护:引号内的神态套话按折扣计分,命中打 dialogue 标记。"""
    inner = "我眼中闪过一丝慌乱,嘴角勾起一抹弧度,空气仿佛凝固了。"
    plain = ai_flavor_report(inner + "他不禁叹了口气。")
    dlg = ai_flavor_report(
        "她说:\u201c我眼中闪过一丝慌乱,嘴角勾起一抹弧度,空气仿佛凝固了。\u201d"
        "他不禁叹了口气。"
    )
    # 正文命中率相同(同一句),但对白版加权分显著低于正文版
    assert dlg.score < plain.score
    assert all(h.dialogue for h in dlg.hits if h.start < dlg.hits[-1].start)
    assert any(h.dialogue for h in dlg.hits)
    # 类别明细带 dialogue_count,供回流分析
    assert dlg.categories["万能神态套话"]["dialogue_count"] >= 1
    # 命中块渲染时带对白警示(重写 prompt 据此保人物声线)
    block = polisher._flavor_hits_block(dlg)
    assert "对白" in block and "保人物声线" in block


def test_euro_chinese_category_detects_translationese():
    """规则扩容:欧化中文(翻译腔)成类命中。"""
    report = ai_flavor_report(
        "随着科技的不断发展,这个平台扮演着重要的角色,发挥着关键的作用。"
        "对于用户而言,这不仅仅是一次升级,而是一场变革。"
    )
    assert "欧化中文(翻译腔)" in report.categories
    cats = report.categories["欧化中文(翻译腔)"]
    assert cats["count"] >= 3


def test_judges_metrics_present_on_chapter_scale():
    """第二裁判:章稿尺度(≥400 汉字)产出 TTR/新颖率/「的」频率/虚词密度。"""
    long_ai = AI_CORPUS[5] * 8   # 拉到章稿长度
    report = ai_flavor_report(long_ai)
    m = report.metrics
    assert m["ttr"] is not None and 0 < m["ttr"] < 1
    assert m["novelty_4gram"] is not None and 0 < m["novelty_4gram"] <= 1
    assert m["de_ratio"] is not None and m["de_ratio"] >= 0
    assert m["funcword_density"] is not None and m["funcword_density"] > 0


def test_judges_metrics_none_on_fragments():
    """短片段(<400 汉字)统计噪声大,指标置 None(复合验收按不倒退处理)。"""
    m = ai_flavor_report("他说走了。天下雨了。").metrics
    assert m["ttr"] is None and m["novelty_4gram"] is None


def test_judges_regress_flags_washed_out_rewrite():
    """复合验收:正则分降了但词汇丰富度被洗没(TTR 大跌)→ 判倒退,不采纳。"""
    def _report(**metrics) -> FlavorReport:
        r = ai_flavor_report("占位")
        r.metrics.update(metrics)
        return r

    before = _report(ttr=0.62, novelty_4gram=0.85, de_ratio=0.03, funcword_density=0.10)
    ok = _report(ttr=0.60, novelty_4gram=0.84, de_ratio=0.032, funcword_density=0.105)
    assert not judges_regress(before, ok)   # 正常去味:指标基本持平

    washed = _report(ttr=0.45, novelty_4gram=0.85, de_ratio=0.03, funcword_density=0.10)
    assert judges_regress(before, washed)   # 人味被洗掉

    de_bloated = _report(ttr=0.62, novelty_4gram=0.85, de_ratio=0.055, funcword_density=0.10)
    assert judges_regress(before, de_bloated)  # 「的」字膨胀

    none_side = _report(ttr=None, novelty_4gram=None, de_ratio=None, funcword_density=None)
    assert not judges_regress(before, none_side)  # 短文本不参与


def test_advice_block_covers_new_judges():
    """统计诊断 advice:覆盖「的」字密度与新颖率两类新问题。"""
    core = "他愤怒的目光扫过的桌面的边缘的木纹的痕迹" * 20   # 高「的」密度
    report = ai_flavor_report(core)
    advice = report.advice_block()
    assert "的" in advice
