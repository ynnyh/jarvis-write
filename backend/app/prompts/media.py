# app/prompts/media.py
# -*- coding: utf-8 -*-
"""周边创作提示词:封面图 / 主题曲。

把项目素材(概念/架构/大纲/简介)喂给 LLM,产出可直接拿去第三方工具生成的
提示词——我们只产"提示词",不接绘图/音乐模型(用户拿去即梦/MJ/Suno 自己跑)。

- COVER_PROMPT   → 3 套封面画面提示词(中文描述 + 英文 MJ 版 + 负面词),风格各异
- ANTHEM_PROMPT  → Suno 主题曲:英文风格标签 + 结构化中文歌词 + 歌名
"""
from __future__ import annotations

# =============== 封面图提示词 ===============
COVER_PROMPT = """\
你是资深的小说封面美术指导,熟悉即梦、Midjourney、Stable Diffusion 等 AI 绘图工具的提示词写法。
请根据下面这本书的素材,产出 3 套风格差异明显的封面画面提示词。

【书名】{title}
【类型】{genre}
【一句话主题】{topic}
{concept_block}{core_seed}{synopsis_block}{outline_block}
严格按 JSON 输出(不要 markdown 围栏,不要任何解释),结构如下:
{{
  "covers": [
    {{
      "style": "这套方案的画风一句话概括(如:国风工笔插画 / 电影感写实 / 赛博霓虹)",
      "prompt_cn": "给即梦等中文绘图工具的完整中文提示词",
      "prompt_en": "给 Midjourney 的英文提示词(逗号分隔的关键词串,可含 --ar 2:3 等参数)",
      "negative": "负面提示词(不希望出现的元素,中文逗号分隔)"
    }}
  ]
}}

要求:
1. 出 3 套方案,画风彼此差异要大(如:一套写实电影感、一套国风/插画、一套氛围抽象),各贴合本书基调。
2. prompt_cn 要素齐全:主体人物(外形/服饰/神态)、场景环境、氛围情绪、光影、构图视角、画风。
   描述具体可画,不要空泛(不写"很震撼",而写"逆光剪影,暖金色夕照,低角度仰拍")。
3. prompt_en 是 prompt_cn 的地道英文关键词版,便于直接粘进 Midjourney;结尾可给 `--ar 2:3`(竖版封面)。
4. negative 给常见需规避项:多余文字水印、扭曲手部、多余肢体、低分辨率等,并结合本书调性补充。
5. 封面是竖版书封比例(约 2:3),构图要给主标题留出上/下方空间;画面里不要出现任何文字。
6. 不剧透关键反转;只呈现能勾人的核心意象与主角气质。
"""


# =============== 主题曲提示词(Suno) ===============
ANTHEM_PROMPT = """\
你是同时精通网文与音乐创作的词曲策划,熟悉 Suno 这类 AI 音乐工具的提示词写法。
请根据下面这本书的素材,创作一首贴合本书气质的主题曲,让作者可以直接拿去 Suno 生成。

【书名】{title}
【类型】{genre}
【一句话主题】{topic}
{concept_block}{core_seed}{synopsis_block}{outline_block}
严格按 JSON 输出(不要 markdown 围栏,不要任何解释),结构如下:
{{
  "song_title": "主题曲歌名(中文,呼应书名与主题,朗朗上口)",
  "style_tags": "给 Suno 的英文风格标签串(逗号分隔:曲风 + 情绪 + 人声 + 配器 + 速度)",
  "lyrics": "带结构标记的中文歌词,标记用英文([Verse 1] [Pre-Chorus] [Chorus] [Bridge] [Outro]),歌词正文中文",
  "vibe": "一句话说明这首歌想传达的整体氛围与它如何呼应本书"
}}

要求:
1. style_tags 用英文(Suno 对英文风格标签识别最好),涵盖:曲风(如 cinematic, epic orchestral, pop ballad,
   guzheng folk, synthwave 等)、情绪(如 melancholic, heroic, tense)、人声(如 female vocal, male vocal,
   duet)、关键配器、速度/节奏(如 slow, driving beat)。5-10 个标签,贴合本书基调。
2. lyrics 是中文歌词,但段落结构标记必须用英文方括号(Suno 识别):至少含 [Verse 1]、[Chorus],
   可加 [Verse 2]、[Pre-Chorus]、[Bridge]、[Outro]。副歌要有记忆点、可重复。
3. 歌词要扣住本书的核心意象、主角处境与情感内核,像是"这本书的主题曲",不要写成泛泛情歌。
4. 不剧透关键反转;用意象和情绪表达,不直白复述剧情。
5. 歌词长度适中(适合一首 3-4 分钟的歌),不要过长。
"""
