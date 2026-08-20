# app/prompts/dna_capsules.py
# -*- coding: utf-8 -*-
"""味道锚胶囊 & 题材配对反例:概念/方向层的「正向锚定」素材。

这是正文层 prompts/style_capsules 在「故事味道」维度的对应物,专治题材/口味漂移:
选了「青春校园」却生成「学生觉醒异能」——不是模型不会写现实,而是缺一个「该是
哪种味道」的正样本,只能在自己「全网文平均」的默认分布上发挥,而平均恰恰最容易
滑向套路(系统/觉醒/重生/金手指)。本模块补两类正样本:

1. 味道锚胶囊(DNACapsule):一批辨识度高的「故事味道」,每个含
   - directive:该味道的题材质感 / 情感基调 / 场景取向 / 冲突来源(学「要哪种味道」)
   - sample:一段「那个味道」的原创短场景示范(few-shot 正例——给 pattern 比给 rule 有效)
   - mode / axes:选中即预填 DNA 的题材模式与味道轴(把「认领味道」一步落成坐标)

   **GIGO 关键**:用户说「像《最好的我们》」时,不依赖模型对该书的记忆(记不准=脏输入),
   而是让用户在下拉里认领一个手写胶囊,注入的是我们打磨过的 directive+sample。
   comps_hint 仅供用户在 UI 里辨认「这是哪一挂」,**不注入 prompt**(书名不喂给模型)。

   版权红线(沿用 style_capsules 纪律):sample 一律本项目自撰的原创短段,非任何
   原作节选;comps_hint 只作参照指路名,不搬其内容。前端须标注「味道参考·非原作节选」。

2. 题材配对反例(GENRE_PAIRWISE):✗跑偏写法 → ✓对味写法 的对照,尤其针对现实向被
   网文惯性带成异能/系统/重生的高频翻车点。给「pattern」而非「rule」,注入生成/自愈重写。

注入路径:顺 style_directives / style_block 追加,不新增 prompt 占位符(与 style_capsules
一致:一处注入多处生效,避免 format 只改一处导致 KeyError)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DNACapsule:
    key: str          # 稳定标识(存进 project.dna.taste_key)
    name: str         # 展示名
    comps_hint: str   # 「像哪些作品一类」——仅供用户在下拉里认领,不注入 prompt
    mode: str         # 建议题材模式 realistic/fantasy/mixed(选中即预填 DNA.mode)
    directive: str    # 味道特征(题材质感/情感基调/场景取向/冲突来源;学「要哪种味道」)
    sample: str       # 味道示范(原创短场景,非原作节选)
    axes: dict = field(default_factory=dict)  # 建议味道轴预填(选中即预填 DNA.axes)


# 一批味道锚胶囊。所有 sample 均为「那个味道」的原创短段,非任何原作节选。
# 排序即前端下拉展示顺序。覆盖主要味道区间;现实青春列首(题材漂移重灾区,首要示范)。
DNA_CAPSULES: list[DNACapsule] = [
    DNACapsule(
        key="youth_realism",
        name="现实青春·疼痛细腻",
        comps_hint="《最好的我们》《你好，旧时光》一类的现实校园成长",
        mode="realistic",
        directive=(
            "现实校园里的初恋、暗恋与成长,克制、细腻、有生活质感。"
            "冲突全部来自现实——升学压力、家庭、距离、误会、时间与错过,"
            "绝不出现超能力/系统/重生/穿越等非现实设定。"
            "情绪藏在日常细节里(晚自习、走廊、操场、一张没递出去的纸条),"
            "不喊口号、不开金手指;基调偏真,甜里带涩,常以「圆满的遗憾/遗憾的圆满」收束。"
        ),
        sample=(
            "晚自习的灯管嗡嗡响,她第三次把同一道题的答案抄错。\n"
            "前排那个人回头借橡皮,她「嗯」了一声,把整块新的都推了过去。\n"
            "他只掰了一半还回来。剩下那半,她一直没舍得用。"
        ),
        axes={"pace": "慢", "sweetness": "甜里带涩", "realism": "写实",
              "ending": "遗憾", "drama": "日常"},
    ),
    DNACapsule(
        key="sweet_dopamine",
        name="甜宠恋爱·轻松上头",
        comps_hint="轻松向甜宠、下饭恋爱一类",
        mode="mixed",
        directive=(
            "双向奔赴、糖分高、节奏轻快、强互动;误会不隔夜。"
            "主打愉悦与「上头」,冲突小而甜(吃醋、试探、护短),不折磨读者;"
            "两个人越靠越近的过程本身就是爽点,情绪向上、明亮。"
        ),
        sample=(
            "「你昨天跟谁看电影?」他问得漫不经心,手却把她那杯奶茶挪到了自己这边。\n"
            "「我妹。」\n"
            "他「哦」了一声,把奶茶又推了回去,还多推了自己那杯。"
        ),
        axes={"pace": "快", "sweetness": "甜", "ending": "圆满", "drama": "日常"},
    ),
    DNACapsule(
        key="tragic_deep",
        name="虐恋深情·意难平",
        comps_hint="高虐、BE 美学、意难平一类",
        mode="mixed",
        directive=(
            "命运弄人、错过与误解、深情而不得。情感浓烈,但笔要克制——"
            "用留白、细节与克制的对话让读者自己心碎,不靠形容词堆砌硬煽;"
            "常以意难平或苦涩收束,痛点落在「差一点」上。"
        ),
        sample=(
            "他终于赶到站台,火车刚好开动。\n"
            "隔着车窗,她笑了笑,嘴型像是「别追了」。\n"
            "他站住了。后来很多年,他都恨自己那天为什么就真的站住了。"
        ),
        axes={"sweetness": "虐", "ending": "遗憾", "drama": "戏剧化"},
    ),
    DNACapsule(
        key="xuanhuan_blood",
        name="玄幻热血·爽感升级",
        comps_hint="境界流、升级打脸爽文一类",
        mode="fantasy",
        directive=(
            "自洽的境界/力量体系,越级挑战、逆袭打脸、奇遇突破;爽点密集、格局渐大。"
            "节奏明快,该爽就爽——这是该题材的「对味」,不要写成寡淡文艺腔;"
            "但爽要有铺垫和代价,不是无脑碾压。"
        ),
        sample=(
            "「就凭你?」对方嗤笑。\n"
            "他没答话,抬手一掌。石阶碎了三级,那人被钉在柱子上,半天没滑下来。\n"
            "满场哗然——三个月前,这人连外门弟子都不是。"
        ),
        axes={"pace": "快", "drama": "戏剧化", "realism": "梦幻"},
    ),
    DNACapsule(
        key="suspense_cold",
        name="悬疑冷硬·抽丝剥茧",
        comps_hint="社会派/硬核推理、冷硬悬疑一类",
        mode="realistic",
        directive=(
            "冷峻、逻辑严密、氛围压抑;线索早埋、公平给读者,真相层层剥开。"
            "克制不煽情,靠细节、反差与反转推进;不靠灵异外挂解题(除非明确灵异向)。"
            "每一个巧合背后都要有可追溯的因果。"
        ),
        sample=(
            "死者的表停在三点一刻,可他的手机最后一次亮屏是四点零二。\n"
            "警官把两样东西并排放在桌上,很久没说话。\n"
            "「有人,」他终于开口,「想让我们相信他死得更早。」"
        ),
        axes={"pace": "稳", "realism": "写实", "drama": "戏剧化"},
    ),
    DNACapsule(
        key="history_heavy",
        name="历史厚重·家国沉浮",
        comps_hint="正剧向历史、家国群像一类",
        mode="realistic",
        directive=(
            "考据感、厚重,命运与时代交织,群像与权谋并重。基调沉稳,细节有时代质感;"
            "个人命运扣在大势上,一步错步步错。避免现代梗与穿越金手指(除非明确穿越向)。"
        ),
        sample=(
            "圣旨到的时候,他正在给老母亲熬药。\n"
            "他跪下接旨,手很稳,药却在身后咕嘟咕嘟地滚。\n"
            "他知道,这碗药熬完,这个家往后就要各奔东西了。"
        ),
        axes={"pace": "稳", "realism": "写实", "drama": "戏剧化", "ending": "遗憾"},
    ),
    DNACapsule(
        key="healing_warm",
        name="治愈日常·细水温情",
        comps_hint="生活流、治愈系、烟火气一类",
        mode="mixed",
        directive=(
            "慢节奏、生活流、温情、烟火气;少大冲突、多细腻情绪与日常质感。"
            "以小见大,靠人与人之间的善意与微光打动人,不刻意煽情;"
            "读完让人心里暖一下、松一口气。"
        ),
        sample=(
            "早点摊的阿婆记得每个熟客的口味。\n"
            "「今天加个蛋,」她把豆浆推过来,「你昨天咳嗽,我听见了。」\n"
            "他愣了一下,忽然觉得这座陌生的城市,好像也没那么冷。"
        ),
        axes={"pace": "慢", "sweetness": "甜", "drama": "日常", "ending": "圆满"},
    ),
    DNACapsule(
        key="scifi_idea",
        name="硬核科幻·点子驱动",
        comps_hint="点子流、思辨科幻一类",
        mode="fantasy",
        directive=(
            "设定自洽、点子惊奇、冷静思辨、逻辑推演;情感服务于设定震撼与思想冲击。"
            "世界规则一旦立下就严格遵守,不随意打破;震撼来自推演,而非拍脑袋的奇观。"
        ),
        sample=(
            "他们终于破译了那段信号,只有一句话,重复了四百万次。\n"
            "「不要回复。」\n"
            "而人类,已经在三天前回复了。"
        ),
        axes={"realism": "梦幻", "drama": "戏剧化", "pace": "稳"},
    ),
]

_BY_KEY: dict[str, DNACapsule] = {c.key: c for c in DNA_CAPSULES}


def get_dna_capsule(key: str) -> DNACapsule | None:
    """按 key 取味道锚;未知/空 key 返回 None(回退无锚,不报错)。"""
    return _BY_KEY.get((key or "").strip())


def dna_capsule_choices() -> list[dict]:
    """给前端下拉/API 的选项:含 comps_hint / mode / axes,选中即可预填坐标卡。

    不含 sample 正文(前端只需选择;sample 在生成时由后端注入)。
    """
    return [
        {
            "key": c.key,
            "name": c.name,
            "comps_hint": c.comps_hint,
            "mode": c.mode,
            "directive": c.directive,
            "axes": dict(c.axes),
        }
        for c in DNA_CAPSULES
    ]


_DNA_HEADER = (
    "【味道锚(参照这种「故事味道」:题材质感 / 情感基调 / 场景取向 / 冲突来源——"
    "只学「要哪种味道」,不要照搬示范里的具体情节;示范为本项目自撰,非原作节选)】"
)


def render_dna_capsule_block(taste_key: str = "") -> str:
    """把选中的味道锚渲染成注入块(强位正向锚);无/未知 key 返回空串。"""
    cap = get_dna_capsule(taste_key)
    if not cap:
        return ""
    parts = [
        f"目标味道(参照「{cap.name}」的取向与语感):{cap.directive}",
        f"味道示范(只作取向/语感参照,不要照搬其中情节):\n{cap.sample}",
    ]
    return "\n" + _DNA_HEADER + "\n" + "\n".join(parts) + "\n"


# 题材配对反例:✗跑偏(尤其现实向被网文惯性带成异能/系统/重生)→ ✓对味的写法。
# 给「同一处怎么从跑偏改成对味」的 pattern,比抽象禁令有效。按题材模式取用。
GENRE_PAIRWISE: dict[str, list[tuple[str, str]]] = {
    "realistic": [
        (
            "青春校园里,主角在一次意外后觉醒了异能,从此改写命运。",
            "青春校园里,主角在一次意外后和暗恋的人有了交集,心事从此多了起来。",
        ),
        (
            "他打开脑海中的系统面板,签到领取新手大礼包。",
            "他翻开新发的教辅,在扉页角落写下那个人的名字,又赶紧涂掉。",
        ),
        (
            "重生一世,他发誓要弥补上辈子所有的遗憾,走上人生巅峰。",
            "他把那张没送出去的贺卡收进抽屉最里面,告诉自己这次一定要说出口。",
        ),
        (
            "危急关头,神秘老者现身,传授他绝世功法,实力暴涨。",
            "危急关头,是那个平时最不起眼的同桌,替他挡下了那句最难听的话。",
        ),
    ],
    "": [  # 通用:高概念套路化 → 具体化(所有题材通吃)
        (
            "少年踏上了一段充满未知与挑战的冒险旅程。",
            "少年攥着半张被雨泡烂的地址,决定先跳上这趟车再说。",
        ),
        (
            "他的眼中闪过一丝坚定,他知道自己的命运即将改变。",
            "他把录取通知书折了两折,塞进裤兜,没让任何人看见。",
        ),
    ],
}


def genre_pairwise_block(mode: str = "") -> str:
    """渲染题材配对反例注入块(照 ✓ 的对味写法来,别写成 ✗ 那样跑偏)。

    始终带上通用组;现实向额外叠加「别滑向异能/系统/重生」的专项组。
    """
    pairs = list(GENRE_PAIRWISE.get(mode, [])) if mode else []
    pairs += GENRE_PAIRWISE.get("", [])
    if not pairs:
        return ""
    lines = ["【跑偏 → 对味 对照(照 ✓ 的写法来,别写成 ✗ 那样跑题)】"]
    for bad, good in pairs:
        lines.append(f"✗ {bad}")
        lines.append(f"✓ {good}")
    return "\n".join(lines) + "\n"
