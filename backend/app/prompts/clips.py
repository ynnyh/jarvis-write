# app/prompts/clips.py
# -*- coding: utf-8 -*-
"""情绪短片提示词:15/30 秒情绪命题短视频,一次产三个不同切入的本子。

结构铁律(短视频完播率逻辑):前 2 秒钩子 → 中段蓄势 → 最后 3-5 秒反转/戳心 + 金句收尾。
**铁律必须出现在提示词正文里**(早年只写在本文档字符串里,模型根本看不见,
产出的本子开头铺垫两格才进正题——这是"内容总不满意"的主因之一)。
通用版纯虚构放飞;小说衍生版为投流种草片,金句必须能在提供的正文节选里找到——
引擎还会做子串溯源校验,编造的引用会被标进 cautions。

**两段式**(为什么不一次出完):一次要吐「风格卡 + 3 个本子 × 最多 7 格 × 三轨提示词」
是全站最大的单次输出,一旦被 max_tokens 砍断,三个本子一起白跑。拆成:
①`CLIPS_TAKES_PROMPT` 定风格卡 + 三条切入(小输出,不容易截断);
②`CLIPS_EXPAND_PROMPT` 三发并行,各自把一条切入展开成分镜与提示词,各自重试,
  一发失败还剩两个可用本子(引擎要求至少 2 个才算成功)。
风格卡必须在①里定死、原样传进②:三个本子共用一套画风是产品红线——
用户三选一之后,拿到的提示词口径必须和另外两个一致。

**带反馈重生成**:换一批可带用户意见(feedback)与上一批切入摘要,这批要避开旧方向、
落实意见;单条重拍(reexpand)带意见进②,风格卡与切入保持不变只重展开。
"""
from __future__ import annotations

# 两段共用的结构铁律:完播率逻辑必须让模型看见,不是给维护者看的注释
CLIPS_STRUCTURE_RULES = """\
**结构铁律**:
- 第一格 2 秒内钩住:最有张力的画面或第一句话直接开场,不许先铺环境、不许缓起;
- 中间每格必须推进新信息或新情绪,同一情绪状态不许连拍两格;
- 最后一格停在金句的画面上:留白,不解释、不总结,画面里不写字。"""

# 俗套黑名单:这些桥段被拍烂了,出现即平庸(除非给出明确翻新,否则禁用)
CLIPS_CLICHE_BLACKLIST = """\
**俗套黑名单**(除非翻出新意,否则禁用):雨中痛哭、转身回头慢镜头、牵手奔跑、
望向天空、照片定格闪回、「如果当初/珍惜眼前人」式字幕。"""


def dialogue_style_rule(style: str) -> str:
    """台词风格(用户导向维度)→ 提示词硬约束。auto 返回空(模型自定)。"""
    return {
        "voiceover": (
            "**台词风格=旁白独白主导**:至少三分之二的 line 是旁白,"
            "角色开口至多一句点缀;旁白要有私人语气,不是解说词"
        ),
        "dialogue": (
            "**台词风格=角色对话主导**:靠两个人你来我往推进情绪,"
            "旁白至多起头/收尾各一句;对白要短、要抢话,不许大段独白"
        ),
        "silent": (
            "**台词风格=无台词**:lines 留空、每格 dialogue 留空,"
            "信息全靠画面、动作与物件传递,唯一的文字是末格金句字幕卡;"
            "静默本身就是风格,画面必须更锐利"
        ),
    }.get(style, "")


def pacing_rule(pacing: str) -> str:
    """节奏(用户导向维度)→ 提示词硬约束。auto 返回空。"""
    return {
        "hook_first": (
            "**节奏=爆点前置**:第一格就把冲突或悬念怼到观众脸上,"
            "之后每格持续给新料,情绪不许回落"
        ),
        "slow_burn": (
            "**节奏=层层蓄势**:前面克制铺陈,一格比一格重,"
            "情绪顶点放在倒数第二格,末格骤然收静"
        ),
        "twist_end": (
            "**节奏=结尾反转**:前面按生活惯性叙事埋细节,最后一格翻转视角或真相,"
            "punchline 随反转落地——反转必须提前有伏笔,不许凭空翻"
        ),
    }.get(pacing, "")


def intensity_rule(intensity: str) -> str:
    """情绪浓度(用户导向维度)→ 提示词硬约束。auto 返回空。"""
    return {
        "restrained": "**情绪浓度=克制留白**:情绪只给七分,靠物件、光线、停顿说话,禁止哭喊与抱头痛哭",
        "intense": "**情绪浓度=浓烈直给**:画面与台词都要有冲击力,顶点一格放满,宁过勿温",
    }.get(intensity, "")


# =============== 背景块:两段共用,按入口二选一 ===============
CLIPS_GENERIC_CONTEXT = """\
【情绪命题】{theme_label}
【时长】{duration_s} 秒(竖屏 9:16)|【画风方向(硬约束)】{direction_directive}
{steering_block}{inspiration_block}"""

CLIPS_NOVEL_CONTEXT = """\
这是一支**投流种草短视频**:用 15-30 秒把一本书最戳人的地方剪给潜在读者看,
让人 3 秒停下滑动的手、看完想去搜书名。

【书名】{title}
【类型】{genre}
【一句话主题】{topic}
{concept_block}
【正文节选(金句与名场面只可从这里选/轻改,不得编造)】
{excerpts_block}
【角色锚(出场角色的外貌以此为准,逐字嵌入提示词)】
{characters_block}
【时长】{duration_s} 秒(竖屏 9:16)|【画风方向(硬约束)】{direction_directive}
{steering_block}{inspiration_block}"""

# 小说衍生独有的金句红线(通用入口这一块为空)
CLIPS_GROUNDING_RULE = """\
**金句红线**:punchline 与核心台词必须来自正文节选或轻度口语化改写,
`quote_source` 逐字抄原句;节选里没有的,不许编(引擎会做子串溯源校验)。
"""

# =============== ① 定风格卡 + 三条切入 ===============
CLIPS_TAKES_PROMPT = """\
你是爆款情绪短视频的导演兼文案,做过千万播放的 15 秒戳心片。
{context_block}
先只做两件事:**定一套三条本子共用的画风**,再给 **3 个真正不同切入的创意方向**。
这一步不要写分镜、不要写提示词(下一步再展开)。
{structure_rules}
{cliche_blacklist}{feedback_block}
严格按 JSON 输出(不要 markdown 围栏,不要任何解释):
{{
  "style_name": "风格名一句话",
  "style_cn": "画风锁定段 60-100 字(媒介/色彩/光影/质感,不绑定具体内容)",
  "style_en": "英文关键词串 15-25 词",
  "negative": "负面提示词(通用规避 + 本风格特有)",
  "takes": [
    {{
      "take": "切入一句话(如「未说出口的道歉」,10 字内)",
      "logline": "这个本子讲什么,一句话",
      "emotion_curve": "从什么情绪起步、在哪一格到顶、结尾落在哪(如「平静→屏息→刀落般的空」,40 字内)",
      "punchline": "结尾金句字幕卡(≤16 字,戳心但不说教)",
      "hook_text": "开头 3 秒的钩子文案(≤16 字,必须对应第一格画面)",
      "quote_source": "{quote_hint}"
    }}
  ]
}}

要求:
1. 必须给满 3 条切入,且切入真正不同(不同人物关系/不同时空/不同反转方向),
   不是同一个故事的三种说法。
2. punchline 是灵魂:口语、具体、有画面,拒绝「珍惜眼前人」式正确废话;三条各不相同。
3. style_cn/style_en/negative 必须落在画风方向内,且不绑定任何一条切入的具体内容
   ——三条本子都要靠它统一画风。
4. hook_text 是完播率的生死线:第一格画面/第一句台词的钩子感要能一句话讲清,
   三条的钩子各走不同的路(画面冲突/台词暴击/悬念反常)。
{grounding_rule}"""

# =============== ② 单条切入展开成分镜(三发并行) ===============
CLIPS_EXPAND_PROMPT = """\
你是爆款情绪短视频的导演兼分镜师。
{context_block}
{structure_rules}
{cliche_blacklist}
【已定的画风(逐字用,不得另立一套)】
- 中文画风锚:{style_cn}
- 英文画风锚:{style_en}
- 负面词基座:{negative}

【这一条要拍的切入(不许换故事)】
- 切入:{take}
- 一句话:{logline}
- 情绪曲线:{emotion_curve}
- 结尾金句(末格停在它的画面上,画面里不写字):{punchline}
{feedback_block}
把它拍成完整分镜。严格按 JSON 输出(不要 markdown 围栏,不要任何解释):
{{
  "lines": [
    {{"speaker": "旁白或角色名", "text": "台词/旁白,单句 ≤ 20 字", "action": "这句对应的画面,25 字内"}}
  ],
  "shots": [
    {{"seq": 1, "scene_name": "场景", "characters": [],
      "action_desc": "这一格画面,40 字内,必须可画",
      "shot_type": "远景/全景/中景/近景/特写", "camera": "固定/推/拉/摇/跟随/环绕",
      "dialogue": "该镜头承载的台词(与 lines 对齐,可空)", "duration_s": 3,
      "prompt_cn": "静帧中文提示词 80-140 字,含中文画风锚(逐字)、主体/场景/氛围/光影/构图(竖版9:16)",
      "prompt_en": "英文提示词 25-40 词,含英文画风锚,结尾 --ar 9:16",
      "negative": "负面基座并入 + 本格特有"}}
  ]
}}

要求:
1. 节奏:{duration_s} 秒 = {shot_hint};第一格就是钩子(最有张力的画面或第一句话),
   最后一格停在 punchline 的画面上(留白,不加字)。
2. 台词总字数按 4 字/秒贴着时长;宁少勿多,留白也是情绪。每条 line 落进某个镜头的 dialogue。
3. prompt_cn/prompt_en 里画风锚逐字保留;空词(震撼/唯美)禁用。
4. 人物最多 2 人;同一个人在各格里外貌一致(同一身衣服)。
5. 每一格的景别都要有变化(连续两格不许同景别+同运镜);近景/特写留给情绪顶点。
{grounding_rule}"""


def takes_feedback_block(prev_takes: list[dict], feedback: str) -> str:
    """换一批时的反馈块:上一批切入摘要 + 用户意见(空反馈返回空串)。"""
    if not feedback.strip():
        return ""
    lines = [
        f"\n【上一批的切入(用户不满意,这批避开它们的方向,除非用户明确要求保留)】"
    ]
    for i, t in enumerate(prev_takes or [], 1):
        take = str(t.get("take") or "").strip()
        logline = str(t.get("logline") or "").strip()
        if take or logline:
            lines.append(f"{i}. {take}——{logline}")
    lines.append(f"【用户意见(最高优先级,逐条落实)】{feedback.strip()}")
    lines.append("这批三条切入必须与上一批真正不同,并落实用户意见。")
    return "\n".join(lines)


def expand_feedback_block(feedback: str) -> str:
    """单条重拍时的反馈块:只重展开、切入与画风不变,意见必须落实。"""
    if not feedback.strip():
        return ""
    return f"\n【用户对本条的意见(重拍时必须落实,切入与画风不许变)】{feedback.strip()}"
