# app/engines/skills/packs.py
# -*- coding: utf-8 -*-
"""创作 Skill 包引擎:官方包 seed、按节点检索、注入块渲染与预算闸(docs/21)。

与 tendency 体系(倾向标签/手法卡/文风画像)的分工:tendency 是「每本书勾什么
生效」的散装约束;Skill 包是「成套、随版本分发的工艺」。三个注入纪律
(docs/21 §3.2,防「31 块全量灌」回潮):

1. 不新增模板占位符之外的模板改动——注入一律渲染成块交给既有 format 槽;
2. 注入预算硬闸:单次生成 ≤INJECT_CHAR_BUDGET 字、单节点 ≤MAX_PACKS_PER_NODE 包;
3. 按节点分发:包条目声明 node,组装时过滤——对白包永远不进分镜,反之亦然。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import SkillPack

logger = logging.getLogger("jarvis-write.skills")

INJECT_CHAR_BUDGET = 1500   # 单次生成 skill 注入总字数上限(含包名行)
MAX_PACKS_PER_NODE = 2      # 单节点同时挂载的包数上限
HISTORY_KEEP = 10           # 每包保留的编辑历史版本数

# 条目形态:directive=指令文本(进 prompt);param=参数(渲染成「键: 值」行);
# ban=排除清单;format=渲染工艺开关(不进 prompt,由引擎读 pack_key 分支)
VALID_KINDS = {"directive", "param", "ban", "format"}
VALID_NODES = {"idea", "outline", "draft", "polish", "shots", "render"}

# 线的标识:既是包的 scope 值,也决定 active_packs 查哪条线的包。
# 门禁 test_pack_scopes_are_known 拿它当白名单——新增一条线必须先加进来,
# 否则包会写了个谁都不认的 scope 静默永不生效。
VALID_SCOPES = {"novel", "anime", "drama", "promo", "clips", "birthday", "series"}

ANIME_SHOTCARD_PACK_KEY = "anime-shotcard-render"
DRAMA_SHOTCARD_PACK_KEY = "drama-shotcard-render"
CLIPS_COMPACT_PACK_KEY = "clips-compact-render"

# ---- 首批官方包(docs/21 §4;seed 幂等,用户改过的一律不覆盖) ----
BUILTIN_PACKS: list[dict] = [
    {
        "pack_key": "storyboard-basics",
        "name": "分镜功底包",
        "description": "分镜工序的工艺下限:单镜一个主动作、时长纪律、景别交替、镜间衔接。作用于分镜生成。",
        # 三条出片线的分镜在犯同一个错:一格里塞多个动作、镜与镜硬跳。
        # 那是「正确性下限」不是「审美偏好」——写错就会出片跳戏,所以默认开、
        # 三线共用一份(而不是 drama/promo 各抄一个改版,抄一遍口径必分叉)。
        "scope": ["anime", "drama", "promo"],
        "entries": [
            {"node": "shots", "kind": "directive",
             "directive": "每镜只安排一个主动作,把动作写到「怎么做」(肢体/视线/节奏);"
                          "相邻镜头必须有衔接逻辑(动作顺势接/视线引导/遮挡转场),不许硬跳。"},
            {"node": "shots", "kind": "param",
             "params": {"单镜时长上限": "5 秒", "每镜主动作": "1 个",
                        "景别交替": "相邻两镜景别不雷同,特写后接中景/全景缓一拍"}},
        ],
    },
    {
        "pack_key": ANIME_SHOTCARD_PACK_KEY,
        "name": "动漫镜头卡渲染工艺包",
        "description": "整集提示词换镜头卡工艺:一镜一卡(画面卡 60-120 字+运动卡零外貌词),"
                       "身份靠定妆照/末帧首帧钉死,负面词逐镜独立——治「提示词很长、出片很差」。",
        "scope": ["anime"],
        "entries": [
            {"node": "render", "kind": "format",
             "directive": "镜头卡制:逐镜产出画面卡/运动卡/音色/规避项,引擎逐镜给首帧指引"
                          "(定妆照或上一镜末帧);替换旧的分段长文模式。"},
        ],
    },
    # ==================== 出片线三包(docs/25 §2) ====================
    # 共同约定:**内容口径类包默认 enabled=False**,靠用户到「设置 → Skill 包」
    # 显式开启(或书级 mounted_packs 挂载)。理由与爽文双包同源——包一开就改写
    # 生成结果,默认开等于让老用户无预警换口径;而"看得见、改得了、关得掉"
    # 是 docs/21 给这套机制的承诺,兑现它就得默认不打扰。
    # 例外是**工艺收口类**包(下面 drama-shotcard-render / clips-compact-render):
    # 它们要么只是把已经正确的口径单点化,要么是作者 2026-09-28 明确拍板要默认开的。
    {
        "pack_key": DRAMA_SHOTCARD_PACK_KEY,
        "name": "漫剧镜头卡渲染工艺包",
        "description": "漫剧渲染工艺的单点化:一格一卡 + 身份靠锚段与首帧图钉死 + 负面词逐格独立。"
                       "关闭时回落分段长文工艺(仅提示词形态不同,不影响导出)。",
        "scope": ["drama"],
        "entries": [
            {"node": "render", "kind": "format",
             "directive": "一格一卡:每格只出一个镜头、只写一个主动作。身份靠锚段与首帧图钉死——"
                          "运动指令里一个外貌词都不要写(长相已由首帧图定死,复述会让模型"
                          "重画脸、人物一致性当场报废)。负面词逐格独立:共享基座 + 本格规避。"},
        ],
    },
    {
        "pack_key": "drama-scene-punch",
        "name": "漫剧场次爽点包",
        "description": "只管「能不能拍」:爽点落在哪一句台词/哪一个动作上(要能标秒)、"
                       "冲突双方各自的短句、场末留未解决的麻烦。与爽文包的分工见下。",
        "scope": ["drama"],
        # 内容口径类默认关(docs/25 §2.2):包一开就改写生成结果,默认开等于让
        # 老用户无预警换口径;用户在「设置 → Skill 包」显式开启。
        "enabled": False,
        # 与 drama_source_* 爽文包不重复:爽文包管小说文体(对话七成/打脸三件套/face-slap),
        # 本包管分场后的可拍性(标秒/禁心理独白/禁特效写法)。两者可同时挂载。
        "entries": [
            {"node": "draft", "kind": "directive",
             "directive": "每场戏必须写明三件事:①爽点落在哪一句台词或哪一个动作上(要能标出秒数)"
                          "②冲突双方是谁、双方各自的短句台词 ③场末留一个未解决的麻烦,禁套话收尾。"},
            {"node": "draft", "kind": "param",
             "params": {"单场时长": "≤50 秒", "每场爽点数": "1 个(多则都不响)",
                        "可拍性": "禁心理独白/禁环境渲染段/禁必须特效才能实现的写法"}},
        ],
    },
    {
        "pack_key": "promo-hook-3s",
        "name": "宣传片前3秒钩子包",
        "description": "口播类短视频的开工纪律:前 3 秒必有钩子、每镜一个信息点、按语速配字数。",
        "scope": ["promo"],
        "enabled": False,
        "entries": [
            {"node": "outline", "kind": "directive",
             "directive": "前 3 秒必须有一个钩子:反常识结论/具体数字/现场冲突三选一,"
                          "不许用背景铺垫开场。每个镜头只推进一个信息点,不许一句话塞两个意思。"},
            {"node": "outline", "kind": "param",
             "params": {"前3秒钩子": "必有", "单镜信息点": "1 个", "解说词语速": "≤4.5 字/秒"}},
        ],
    },
    {
        "pack_key": "promo-landmark-guard",
        "name": "宣传片素材点红线包",
        "description": "解说词里的事实必须能在素材点里找到出处——治编造数字与机构名。",
        "scope": ["promo"],
        "enabled": False,
        "entries": [
            {"node": "draft", "kind": "directive",
             "directive": "解说词里的每一个事实、数字、机构名、时间,必须能在【素材点】里找到出处;"
                          "找不到就改成不依赖具体事实的表述,禁编造。素材点为空时只写观点不写数据。"},
            {"node": "draft", "kind": "ban",
             "ban_list": ["未提供出处的具体数字", "未提供出处的机构或产品名", "绝对化用语(第一/唯一/最)"]},
        ],
    },
    {
        "pack_key": "clips-emotion-curve",
        "name": "情绪短片情绪曲线包",
        "description": "一个本子只讲一个情绪转折:前段压抑→中段爆发→尾段留钩,峰值落在 60% 之后。",
        "scope": ["clips"],
        "enabled": False,
        "entries": [
            {"node": "draft", "kind": "param",
             "params": {"情绪曲线": "前1/3压抑 → 中段爆发 → 尾段留钩",
                        "单本时长": "15/30 秒", "情绪峰值出现点": "≥60% 处"}},
            {"node": "draft", "kind": "directive",
             "directive": "一个本子只讲一个情绪转折,禁平铺直叙;结尾停在最锋利的一句台词上,不收总结。"},
        ],
    },
    # 作者 2026-09-28 拍板(甲方案):默认开,把视频提示词从「只设下限、上不封顶」
    # 改成「紧凑封顶 + 必填五项清单」。详见 docs/25 §3.2 与 docs/24 裁定。
    # 为什么默认开而上面几个默认关:这条改的是一条**已被实测证伪**的口径
    # (作者原话「提示词很长,生成的内容很差」),且与 clips 自己的台词口径
    # 「宁少勿多」直接冲突——留着等于让同一份提示词里两个相反指令打架。
    # 仍保留关闭入口:用户可回落到旧长文口径自己对比。
    {
        "pack_key": CLIPS_COMPACT_PACK_KEY,
        "name": "短片紧凑封顶渲染包",
        "description": "视频提示词由「只设下限、上不封顶」改为「紧凑封顶 + 必填五项清单」。"
                       "细节靠字段化清单保,不靠字数保。关闭即回落旧长文口径。",
        "scope": ["clips"],
        "entries": [
            {"node": "render", "kind": "format",
             "directive": "画面描述紧凑封顶,不设下限:宁少勿多——视频模型对长提示词是抽样执行,"
                          "写得越满丢得越多,凑字数只会稀释有效指令。必写五项缺一不可:"
                          "主体动作 / 景别运镜 / 光线氛围 / 人物一致性 / 结尾定格。"},
            {"node": "render", "kind": "param",
             "params": {"画面描述字数": "封顶(见 length_guide)", "必填项": "5 项缺一不可"}},
        ],
    },
    # ---- 漫剧源书爽文双包(docs/23 §3.2)。互斥挂载:开书选频道时由建书流程
    # 按书的 audience 挂其一(mounted_packs);全局默认停用,普通书零污染。
    # 公共骨架(outline/draft/polish)两包同文,刻意重复:改男频包不污染女频包。
    {
        "pack_key": "drama_source_male",
        "name": "男频爽文包(漫剧源书)",
        "description": "漫剧源书·男频口径:点子按爆款四件套出,章纲按爽点循环编,"
                       "正文对话密集可拍,审校查爽点兑现。选「写漫剧剧本·男频」开书时自动挂载。",
        "scope": ["novel", "drama"],
        "enabled": False,
        "entries_version": 2,  # 剧本体工艺(双爽点/打脸三件套/文体示范):v1→v2 官方升级
        "entries": [
            {"node": "idea", "kind": "directive",
             "directive": "点子按爆款四件套出:①被低估的主角(身份/年龄/地位至少一层反差)"
                          "②可视化金手指(图鉴/系统/阶位/清单——把变强变成看得见的收集进度)"
                          "③正义动机(复仇/守护/讨公道,冲突要有理由)④信息差底牌(观众知道、"
                          "剧中人不知道)。题材皮肤优先从清单选:扮猪吃虎/系统流/玄幻修仙/"
                          "赶山狩猎/种田经营/末世求生/都市战神/职场打脸/历史权谋/高武御兽。"
                          "爽点侧重:碾压感/收集感/权力感。骨架固定,血肉必须具体且互不雷同。"},
            {"node": "outline", "kind": "directive",
             "directive": "本书是漫剧源书,每章≈一集 50 秒成片,章纲按「双爽点」编:"
                          "①开场三句内进冲突(接上章钩子或新冲突砸下,禁环境铺垫禁回忆)"
                          "②前半章一个小爽点:小打脸/小升级/小揭底,给即时满足"
                          "③中段加压:更强的对手/更大的误解,压抑不超三分之一章"
                          "④后半章一个大爽点当章兑现:大打脸/大揭秘——必须写明冲突双方是谁、"
                          "爽点落在什么具体动作或台词上,连环升级(小打脸引来更大低估,再更大打脸)"
                          "⑤章末钩子停在新麻烦砸下的具体台词或动作上,禁套话。"
                          "每章章纲必须标:冲突双方/小爽点/大爽点/钩子落点四项。"},
            {"node": "draft", "kind": "directive",
             "directive": "漫剧剧本体正文——本条与全文其它小说工艺冲突处(含反AI腔的神态套话禁令),"
                          "一律以本条为准:漫画式表情特写与模式化台词在漫剧里是风格,不是毛病。"
                          "①对话占七成,场景一两笔带过,禁过场段/心理独白段/环境渲染段"
                          "②台词极端化:反派嚣张到脸谱化(「就凭你?」「废物也配?」),"
                          "主角淡定短句反杀,围观者惊叹接话抬气氛"
                          "③打脸现场三件套:对方失态特写(瞳孔骤缩/踉跄后退/脸色僵住)"
                          "+围观哗然或权威背书+一句定格宣言"
                          "④爽点用画面写(升阶的光/砸下的拳/摊开的牌),不写「他很震惊」式评述。"
                          "文体示范(照这个味写):「就凭你这废物,也配碰族长的丹炉?」"
                          "三长老袖子一甩,冷笑堆满脸上。林凡没抬头,指尖火苗一跳,"
                          "丹炉嗡地亮起三纹。满场死寂。三长老脸上的冷笑僵在半路,"
                          "踉跄着退了半步:「三、三纹炼丹?!」「废物?」林凡抬眼,"
                          "火光映着他毫无波澜的脸,「你刚才叫我什么?」"},
            {"node": "polish", "kind": "directive",
             "directive": "漫剧源书审校加查五项:①本章爽点至少两个(小爽点+大爽点),"
                          "分别在第几段兑现②大爽点的打脸三件套齐吗(对方失态特写/"
                          "围观或背书/定格台词)③章末钩子停在具体台词或动作上了吗"
                          "④有没有连续两段无对话的文戏淤积⑤台词够极端吗"
                          "(反派脸谱化嚣张/主角淡定反杀),平掉的台词改锋利。"},
        ],
    },
    {
        "pack_key": "drama_source_female",
        "name": "女频爽文包(漫剧源书)",
        "description": "漫剧源书·女频口径:点子按爆款四件套出,章纲按爽点循环编,"
                       "正文对话密集可拍,审校查爽点兑现。选「写漫剧剧本·女频」开书时自动挂载。",
        "scope": ["novel", "drama"],
        "enabled": False,
        "entries_version": 2,  # 剧本体工艺(双爽点/打脸三件套/文体示范):v1→v2 官方升级
        "entries": [
            {"node": "idea", "kind": "directive",
             "directive": "点子按爆款四件套出:①被低估的主角(身份/年龄/地位至少一层反差)"
                          "②可视化金手指(图鉴/系统/阶位/清单——把变强变成看得见的收集进度)"
                          "③正义动机(复仇/守护/讨公道,冲突要有理由)④信息差底牌(观众知道、"
                          "剧中人不知道)。题材皮肤优先从清单选:大女主复仇/重生虐渣/豪门恩怨/"
                          "马甲大佬/追妻火葬场/年代空间/宅斗宫斗/先婚后爱/穿越种田/萌宝逆袭。"
                          "爽点侧重:情感浓度/关系反转/自我救赎(不靠拯救者,自己破局)。"
                          "骨架固定,血肉必须具体且互不雷同。"},
            {"node": "outline", "kind": "directive",
             "directive": "本书是漫剧源书,每章≈一集 50 秒成片,章纲按「双爽点」编:"
                          "①开场三句内进冲突(接上章钩子或新冲突砸下,禁环境铺垫禁回忆)"
                          "②前半章一个小爽点:小打脸/小升级/小揭底,给即时满足"
                          "③中段加压:更强的对手/更大的误解,压抑不超三分之一章"
                          "④后半章一个大爽点当章兑现:大打脸/大揭秘——必须写明冲突双方是谁、"
                          "爽点落在什么具体动作或台词上,连环升级(小打脸引来更大低估,再更大打脸)"
                          "⑤章末钩子停在新麻烦砸下的具体台词或动作上,禁套话。"
                          "每章章纲必须标:冲突双方/小爽点/大爽点/钩子落点四项。"},
            {"node": "draft", "kind": "directive",
             "directive": "漫剧剧本体正文——本条与全文其它小说工艺冲突处(含反AI腔的神态套话禁令),"
                          "一律以本条为准:漫画式表情特写与模式化台词在漫剧里是风格,不是毛病。"
                          "①对话占七成,场景一两笔带过,禁过场段/心理独白段/环境渲染段"
                          "②台词极端化:反派嚣张到脸谱化(「就凭你?」「废物也配?」),"
                          "主角淡定短句反杀,围观者惊叹接话抬气氛"
                          "③打脸现场三件套:对方失态特写(瞳孔骤缩/踉跄后退/脸色僵住)"
                          "+围观哗然或权威背书+一句定格宣言"
                          "④爽点用画面写(升阶的光/砸下的拳/摊开的牌),不写「他很震惊」式评述。"
                          "文体示范(照这个味写):「就凭你这废物,也配碰族长的丹炉?」"
                          "三长老袖子一甩,冷笑堆满脸上。林凡没抬头,指尖火苗一跳,"
                          "丹炉嗡地亮起三纹。满场死寂。三长老脸上的冷笑僵在半路,"
                          "踉跄着退了半步:「三、三纹炼丹?!」「废物?」林凡抬眼,"
                          "火光映着他毫无波澜的脸,「你刚才叫我什么?」"},
            {"node": "polish", "kind": "directive",
             "directive": "漫剧源书审校加查五项:①本章爽点至少两个(小爽点+大爽点),"
                          "分别在第几段兑现②大爽点的打脸三件套齐吗(对方失态特写/"
                          "围观或背书/定格台词)③章末钩子停在具体台词或动作上了吗"
                          "④有没有连续两段无对话的文戏淤积⑤台词够极端吗"
                          "(反派脸谱化嚣张/主角淡定反杀),平掉的台词改锋利。"},
        ],
    },
]


def ensure_builtin_packs(db: Session) -> None:
    """官方包 seed(幂等):缺哪条补哪条;已存在的(哪怕被用户改过)一律不覆盖。

    spec 里 enabled 缺省 True;爽文双包显式 False——它们靠书级挂载生效
    (Project.mounted_packs,docs/23),全局启用反而会污染普通书。
    """
    existing = {p.pack_key: p for p in db.query(SkillPack).all()}
    for spec in BUILTIN_PACKS:
        row = existing.get(spec["pack_key"])
        if row is not None:
            # 官方包升级:spec 带 entries_version 且高于现版时推进条目——只升
            # 「从未被用户改过」的包(history 空);用户改过的一律不动,版本自主。
            target = int(spec.get("entries_version") or 1)
            if row.is_builtin and not row.history and row.version < target:
                row.history = (row.history or []) + [
                    {"version": row.version, "entries": row.entries}]
                row.entries = [dict(e) for e in spec["entries"]]
                row.version = target
                logger.info("升级官方 Skill 包:%s -> v%d", spec["pack_key"], target)
            continue
        db.add(SkillPack(
            pack_key=spec["pack_key"], name=spec["name"],
            description=spec["description"], scope=list(spec["scope"]),
            entries=[dict(e) for e in spec["entries"]],
            # 初始版本=spec 当前版本:新库直接种到最新,升级分支只服务存量库
            version=int(spec.get("entries_version") or 1), history=[],
            enabled=bool(spec.get("enabled", True)),
            is_builtin=True,
        ))
        logger.info("seed 官方 Skill 包:%s", spec["pack_key"])
    db.commit()


def normalize_entries(entries: object) -> list[dict]:
    """条目归一(API 编辑与 seed 共用一套口径):裁剪、验形态,不合格抛 ValueError。"""
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries 应该是非空数组")
    out: list[dict] = []
    for e in entries[:12]:
        if not isinstance(e, dict):
            continue
        node = str(e.get("node") or "").strip()
        kind = str(e.get("kind") or "").strip()
        if node not in VALID_NODES:
            raise ValueError(f"条目节点「{node}」不在白名单:{sorted(VALID_NODES)}")
        if kind not in VALID_KINDS:
            raise ValueError(f"条目形态「{kind}」不在白名单:{sorted(VALID_KINDS)}")
        item: dict = {"node": node, "kind": kind}
        directive = str(e.get("directive") or "").strip()
        if directive:
            item["directive"] = directive[:600]
        if isinstance(e.get("params"), dict) and e["params"]:
            item["params"] = {
                str(k).strip()[:30]: str(v).strip()[:80]
                for k, v in list(e["params"].items())[:10]
            }
        if isinstance(e.get("ban_list"), list) and e["ban_list"]:
            item["ban_list"] = [str(b).strip()[:40] for b in e["ban_list"][:20] if str(b or "").strip()]
        if kind == "directive" and not item.get("directive"):
            raise ValueError("directive 条目必须有指令文本")
        if kind == "param" and not item.get("params"):
            raise ValueError("param 条目必须有 params 键值")
        if kind == "ban" and not item.get("ban_list"):
            raise ValueError("ban 条目必须有 ban_list 清单")
        out.append(item)
    if not out:
        raise ValueError("entries 里一个可用条目都没有")
    return out


def render_pack_block(pack: SkillPack, node: str | None = None) -> str:
    """单包 → 注入文本块(format 条目是工艺开关,不是 prompt 材料,不渲染)。

    node 非 None 时只渲染该节点的条目——docs/21 纪律 3「按节点分发」的条目级
    落地:多节点包(如爽文双包的 idea/outline/draft/polish)注入哪行只出哪行,
    其余节点绝不捎带(anime 首批包每包单节点,此前未暴露此缺陷)。
    """
    lines: list[str] = [f"《{pack.name}》(v{pack.version})"]
    for e in pack.entries or []:
        if not isinstance(e, dict):
            continue
        if node is not None and e.get("node") != node:
            continue
        kind = e.get("kind")
        if kind == "directive" and (e.get("directive") or "").strip():
            lines.append(f"- {e['directive'].strip()}")
        elif kind == "param" and isinstance(e.get("params"), dict):
            for k, v in e["params"].items():
                lines.append(f"- {k}:{v}")
        elif kind == "ban":
            bans = e.get("ban_list")
            if isinstance(bans, list) and bans:
                lines.append("- 禁止出现:" + "、".join(str(b) for b in bans))
    return "\n".join(lines)


def active_packs(
    db: Session, *, scope: str, node: str, mounted_keys: list[str] | None = None
) -> list[SkillPack]:
    """某线某节点当前生效的包:scope 命中 + 条目覆盖该节点 + (启用或在书级挂载清单内);
    预算闸裁剪。

    mounted_keys 是**书级挂载清单**(Project.mounted_packs,docs/23):None=不管挂载、
    只看全局 enabled(旧行为,anime 线等);传了则「enabled OR pack_key in mounted」——
    漫剧源书的爽文包 seed 为 enabled=False,靠书级挂载生效,普通书(挂载为空)零污染。
    想对某本书关掉挂载的包:清该书 mounted_packs,别动全局开关。

    裁剪顺序:先按单节点包数上限截断,再按整包字数粒度丢弃超预算的——
    宁可少注入一包,不把两条工艺各注一半(半截约束比没有更糟)。
    """
    ensure_builtin_packs(db)
    mounted = {str(k) for k in (mounted_keys or [])}
    packs = db.query(SkillPack).all()
    hits = [
        p for p in packs
        if scope in (p.scope or [])
        and (p.enabled or p.pack_key in mounted)
        and any(isinstance(e, dict) and e.get("node") == node for e in (p.entries or []))
    ][:MAX_PACKS_PER_NODE]
    within: list[SkillPack] = []
    used = 0
    for p in hits:
        size = len(render_pack_block(p, node=node))
        if used + size > INJECT_CHAR_BUDGET:
            logger.warning("Skill 包《%s》超出注入预算(已用 %d/%d),本次未注入",
                           p.name, used, INJECT_CHAR_BUDGET)
            continue
        used += size
        within.append(p)
    return within


def render_skill_block(
    db: Session, *, scope: str, node: str, mounted_keys: list[str] | None = None
) -> str:
    """生效包 → 一块可注入文本;没有包生效时返回空串(模板槽吃空串零副作用)。"""
    packs = active_packs(db, scope=scope, node=node, mounted_keys=mounted_keys)
    if not packs:
        return ""
    body = "\n".join(render_pack_block(p, node=node) for p in packs)
    return f"【创作 Skill(启用中的工艺包)】\n{body}"


def render_project_skill_block(db: Session, project, node: str, *, scope: str = "novel") -> str:
    """novel 线书级注入助手:按书的 mounted_packs 渲染某节点注入块。

    各生成工序的组装处一行调用;书没挂包(普通书)返回空串,模板槽吃空串,
    生成结果与接入前逐字一致(零污染)。
    """
    mounted = list(getattr(project, "mounted_packs", None) or [])
    return render_skill_block(db, scope=scope, node=node, mounted_keys=mounted)
