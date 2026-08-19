# app/prompts/style_capsules.py
# -*- coding: utf-8 -*-
"""风格胶囊 & 配对反例:去 AI 味的「正向锚定」素材(此前完全缺失的一块)。

痛点:去 AI 味此前全是负向禁令(别写套话)+ 事后检测,没有任何"该像什么"的
正样本。模型缺了正向锚,只能在自己"全网文平均"的默认分布上微调,而平均恰恰
最"AI"。本模块补上两类正样本:

1. 风格胶囊(StyleCapsule):一批辨识度高的笔法(名家/预设),每个含
   - directive:该笔法的句式/用词/节奏/意象/手法特征(让模型模仿"怎么写")
   - sample:一段"仿写"示范(few-shot 正例锚——给 pattern 比给 rule 有效)
   版权红线:在世作家绝不内置原作节选;sample 一律是本项目自撰的"仿其笔法"
   原创短段,只作语感参照、不代表原作。前端须标注"风格参考·非原作节选"。

2. 配对反例(PAIRWISE):✗AI 高频写法 → ✓人类改法 的对照,注入定稿/去味
   重写。让模型看见"同一处怎么从 AI 腔改成人话",比抽象禁令有效得多。

注入路径:顺 style_block += ... 追加(与 style_memo/cards 一致),不新增 prompt
占位符(见 pipeline/chapter.py 注释:一处注入多处生效,避免模板/format 只改一处
导致 KeyError)。
"""
from __future__ import annotations

from dataclasses import dataclass

# 用户范文注入上限:范本只作语感参照,不必整章塞入,防 prompt 膨胀
_MAX_SAMPLE_CHARS = 1200


@dataclass(frozen=True)
class StyleCapsule:
    key: str        # 稳定标识(存进 project._profile.voice_key)
    name: str       # 展示名
    directive: str  # 笔法特征描述(模仿"怎么写")
    sample: str     # 仿写示范(few-shot 正例;非原作节选)


# 一批风格胶囊。所有 sample 均为"仿其笔法"的原创短段,非任何原作节选。
# 排序即前端下拉展示顺序;先名家(辨识度高)后中性预设(不点名作家)。
CAPSULES: list[StyleCapsule] = [
    StyleCapsule(
        key="luxun",
        name="鲁迅·冷峻反讽",
        directive=(
            "白描为主,句子短而顿挫,克制、冷峻,底下压着反讽与悲悯。"
            "不渲染情绪,靠精确的细节和动作让读者自己去感到;"
            "常用近乎冷漠的语气写沉重的事;偶有一两句看似闲笔的议论,收得极快。"
        ),
        sample=(
            "他站在门口,并不进来。雪落在肩上,他也不掸。\n"
            "我知道他是来借钱的,他也知道我知道。\n"
            "我们于是都不说话,由那点雪自己化了。"
        ),
    ),
    StyleCapsule(
        key="yuhua",
        name="余华·平静叙苦",
        directive=(
            "用平静甚至近乎温和的语气叙述残酷与苦难,情感克制到近乎冷。"
            "句子朴素、口语化、偏短;靠重复和白描积累力量;"
            "极少心理描写,苦难像日常一样平铺直叙,黑色幽默藏在平静底下。"
        ),
        sample=(
            "那年冬天他把牛卖了,回来的路上买了二两酒。\n"
            "他说,牛老了,我也老了。\n"
            "他一路走一路喝,到家时酒喝完了,人也就不难受了。"
        ),
    ),
    StyleCapsule(
        key="wangzengqi",
        name="汪曾祺·淡而有味",
        directive=(
            "散文化,淡而有味,生活气息浓;写吃食、风物、市井,闲笔从容。"
            "白描为主,少用形容词堆叠;节奏舒缓,句子清爽干净;"
            "温润有情却不抒情,把日子本身写得有滋味。"
        ),
        sample=(
            "他家的咸鸭蛋是高邮的,筷子一扎,红油就冒出来。\n"
            "切开的水萝卜摆一小盘,撒点盐,也能下两碗饭。\n"
            "日子就是这么过的,不慌。"
        ),
    ),
    StyleCapsule(
        key="jinyong",
        name="金庸·武侠白话",
        directive=(
            "古典白话,武侠节奏明快利落;对话见人物性格与身份。"
            "动作描写有画面,一招一式交代清楚,不拖泥带水;"
            "叙事带一点章回说书的爽利,该快则快,该顿则顿。"
        ),
        sample=(
            "那汉子也不答话,反手一掌拍出。\n"
            "李四侧身让过,顺势扣住他手腕,低喝:「谁派你来的?」\n"
            "那人额上青筋直跳,咬紧了牙,终究一个字也不吐。"
        ),
    ),
    StyleCapsule(
        key="wangxiaobo",
        name="王小波·冷静荒诞",
        directive=(
            "黑色幽默与冷静的荒诞并存;比喻新奇而精确,常出人意料。"
            "理性、反抒情,越荒唐的事写得越一本正经;"
            "句子干净,偶尔跳出来自嘲一句,举重若轻。"
        ),
        sample=(
            "领导说这都是为我好,我信了,就像信过很多别的话一样。\n"
            "那年我二十一岁,觉得世界像一只上足了发条的闹钟,\n"
            "滴答滴答,响得特别有道理,其实什么也没说。"
        ),
    ),
    StyleCapsule(
        key="hemingway",
        name="海明威·冰山极简(译笔)",
        directive=(
            "冰山理论:只写水面上的动作与对话,情绪压在水下不说破。"
            "句子极简,多用名词和动词,少形容词副词;靠短对话推进;"
            "几乎不写心理活动,让留白与停顿承担分量。"
        ),
        sample=(
            "「还疼吗?」她问。\n"
            "「不疼了。」他说。他其实还疼。\n"
            "他看着窗外,雨已经停了。「我们该走了,」他说,把两只杯子都端进了厨房。"
        ),
    ),
    StyleCapsule(
        key="cooldry",
        name="冷硬克制(硬汉派)",
        directive=(
            "克制、冷、不抒情;让动作和对话说话,不解释人物的感受。"
            "短句为主,信息干脆;环境只写与当下有关、能被感知的细节;"
            "紧张时把节奏收得更短更快。"
        ),
        sample=(
            "他数了数子弹,七发,够了。\n"
            "走廊尽头坏了一盏灯,忽明忽暗。他贴着墙根往前,皮鞋踩过碎玻璃,很轻。\n"
            "门虚掩着。他抬脚,踹开了。"
        ),
    ),
    StyleCapsule(
        key="plain",
        name="素淡白描(平实)",
        directive=(
            "平实、干净、不炫技的白描。用最朴素的词把事情说清楚;"
            "不堆形容词、不上比喻、不升华;"
            "像一个诚实的人安静地讲一件真事,靠事实本身而非修辞打动人。"
        ),
        sample=(
            "母亲把最后一件毛衣叠好,放进箱子。\n"
            "她说,那边天冷,记得穿。我说知道了。\n"
            "火车快开了,她还站在原地,没走。"
        ),
    ),
]

_BY_KEY: dict[str, StyleCapsule] = {c.key: c for c in CAPSULES}


def get_capsule(key: str) -> StyleCapsule | None:
    """按 key 取胶囊;未知/空 key 返回 None(回退无范本,不报错)。"""
    return _BY_KEY.get((key or "").strip())


def capsule_choices() -> list[dict]:
    """给前端下拉/API 的选项(带简介,不含 sample 正文——前端只需选择即可)。"""
    return [{"key": c.key, "name": c.name, "directive": c.directive} for c in CAPSULES]


_VOICE_HEADER = (
    "【文风范本(学这种笔法的句子长短、用词、节奏与留白——只学「怎么写」,"
    "不要搬「写什么」;范本内容不得出现在正文里)】"
)


def render_voice_block(voice_key: str = "", voice_sample: str = "") -> str:
    """把选中的名家/预设胶囊 + 作者自备范文渲染成注入块;二者皆空返回空串。

    两个来源可叠加:选了名家胶囊、又贴了自己的范文时,作者范文优先贴合。
    """
    parts: list[str] = []
    cap = get_capsule(voice_key)
    if cap:
        parts.append(
            f"目标笔法(参照「{cap.name}」的语感,属风格参考、非原作节选):{cap.directive}"
        )
        parts.append(f"仿写示范(只作语感参照,不要照搬其中的内容):\n{cap.sample}")
    sample = (voice_sample or "").strip()
    if sample:
        parts.append(
            "作者自备的文风范本(最优先贴合它的句式、节奏与用词,同样不要照搬内容):\n"
            + sample[:_MAX_SAMPLE_CHARS]
        )
    if not parts:
        return ""
    return "\n" + _VOICE_HEADER + "\n" + "\n".join(parts) + "\n"


# 配对反例:✗AI 高频写法 → ✓人类改法。给"pattern"而非"rule",注入定稿/去味重写。
PAIRWISE: list[tuple[str, str]] = [
    ("他眼中闪过一丝复杂的神色,心里五味杂陈。", "他把烟摁灭在桌角,没接话。"),
    ("她感到一阵无法言喻的绝望。", "她盯着那只空碗,很久没有动筷子。"),
    ("这一刻,他终于明白了坚持的意义。", "他没再说什么,弯腰把散落的工具一件件捡回箱子。"),
    ("月光如水,宛如一层薄纱,仿佛给大地披上了银装。", "月亮很亮,院里的青石板泛着白。"),
    (
        "他的眼神是坚定的,他的信念是不可动摇的,他的脚步是沉稳的。",
        "他脚步没停,一直走到队伍最前面。",
    ),
    ("她沉默片刻,缓缓开口道。", "她想了想,说。"),
    ("空气中弥漫着一种难以言喻的紧张气氛。", "没人说话。墙上的钟走得很响。"),
    (
        "总而言之,这次经历让他成长了许多。",
        "(删去这类总结/升华句,用一个具体动作或场景收束即可。)",
    ),
]


def pairwise_examples_block() -> str:
    """渲染配对反例注入块(照右边改法,不要写成左边)。"""
    lines = ["【AI 腔 → 人话 对照(照 ✓ 的改法来,别写成 ✗ 那样)】"]
    for bad, good in PAIRWISE:
        lines.append(f"✗ {bad}")
        lines.append(f"✓ {good}")
    return "\n".join(lines) + "\n"
