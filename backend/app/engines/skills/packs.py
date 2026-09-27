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

ANIME_SHOTCARD_PACK_KEY = "anime-shotcard-render"

# ---- 首批官方包(docs/21 §4;seed 幂等,用户改过的一律不覆盖) ----
BUILTIN_PACKS: list[dict] = [
    {
        "pack_key": "storyboard-basics",
        "name": "分镜功底包",
        "description": "分镜工序的工艺下限:单镜一个主动作、时长纪律、景别交替、镜间衔接。作用于分镜生成。",
        "scope": ["anime"],
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
    # ---- 漫剧源书爽文双包(docs/23 §3.2)。互斥挂载:开书选频道时由建书流程
    # 按书的 audience 挂其一(mounted_packs);全局默认停用,普通书零污染。
    # 公共骨架(outline/draft/polish)两包同文,刻意重复:改男频包不污染女频包。
    {
        "pack_key": "drama_source_male",
        "name": "男频爽文包(漫剧源书)",
        "description": "漫剧源书·男频口径:点子按爆款四件套出,章纲按爽点循环编,"
                       "正文对话密集可拍,审校查爽点兑现。选「写漫剧剧本·男频」开书时自动挂载。",
        "scope": ["novel"],
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
        "scope": ["novel"],
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
