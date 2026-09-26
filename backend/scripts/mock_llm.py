# -*- coding: utf-8 -*-
"""验证环境的 mock LLM:OpenAI 兼容 /v1/chat/completions(流式 + 非流式)。

配合 scripts/e2e_seed.py + DATABASE_URL 指到临时库做全流程 UI 走查:
provider_configs 直接插行指向本服务(UI 填 127.0.0.1 会被 SSRF 防线拒,插库绕过)。
按提示词关键词返回各链路的契约产物,解析失败由管线 degraded 兜底,不会炸任务:

- 引擎卡   「产出一批「故事引擎卡」」        → {"engines":[{engine,angle,hook}]}
            用户带话重出时(【用户看了上一批后的修改要求】块)把要求原文回显进
            首卡 hook——UI 走查据此断言「反馈真的一路走到了模型」;
- 深化     「深化成一个完整的故事概念」      → 六字段概念 JSON;
- 出方案   「扩展出」                        → {"ideas":[六字段概念],"comparison":"…"};
- 题材推断 「候选大类」                      → {"category":"","genre":"","suggestions":[]};
- 起书名   含「书名」                        → 逐行纯文本(起名链路按行解析);
- 其余(架构/蓝图/主审等)                   → 按关键词回纯文本,退化继续。

用法:python scripts/mock_llm.py            # 默认 127.0.0.1:8766
环境变量 STREAM_DELAY:每个 SSE 块的间隔秒数(默认 0.05,0=瞬时,调大便于
肉眼观察「生成中」状态与 LiveDock)。
"""
import asyncio
import json
import os
import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

import uvicorn

PORT = int(os.environ.get("MOCK_LLM_PORT", "8766"))
STREAM_DELAY = float(os.environ.get("STREAM_DELAY", "0.05"))

app = FastAPI()

_FEEDBACK_MARKER = "【用户看了上一批后的修改要求(本轮最高优先级,每张卡都必须遵守)】\n- "

# 六字段概念(sell 为平台简介第一句):字段集合见 app/schemas/concept.py
_CONCEPT = {
    "logline": "落魄镖师押送一趟不许开箱的险镖,验货夜发现箱中藏着通缉的前朝公主",
    "hook": "规矩与良心的每一次碰撞",
    "twist": "雇主就是当年灭她满门的人",
    "protagonist": "李镖头,四十岁,想金盆洗手却被最后一趟镖锁死",
    "conflict": "江湖规矩要她死,自己良心要她活",
    "setting": "乱世末年,镖局行业凋零",
    "sell": "每一趟不问来路的镖,都是一次良心问价",
}

_ENGINE_CARDS = [
    {"engine": f"测试内核{i}:小人物卷入一场不属于他的风波,靠一手不起眼的绝活破局",
     "angle": "小人物·生计·燃", "hook": f"反差抓人点{i}"}
    for i in range(1, 9)
]

# ---- 动画短剧走查契约(饭团精灵阿丸宇宙,与 UI 走查步骤对齐)----
_ANIME_CAST = [
    {"name": "阿丸", "role": "主角",
     "appearance": "一只圆滚滚的白色饭团精灵,体态像发好的馒头,脸上有两团淡淡的红晕,"
                   "左脸颊一颗芝麻大小的痣(跨集认脸的记忆点)",
     "wardrobe": "蓝色围裙(棉布,深蓝滚边),脖子上挂一把小银勺",
     "personality": "认真到较真,慌张时原地转圈", "catchphrase": "包在我身上!"},
    {"name": "豆包", "role": "配角",
     "appearance": "黄色小黄豆,只有阿丸半个头高,圆身体短手脚,眼睛占脸一半",
     "wardrobe": "红色小斗篷", "personality": "天不怕地不怕,闯祸王", "catchphrase": "看我的!"},
    {"name": "锅盖", "role": "配角",
     "appearance": "灰色铁锅盖成精,扁平圆身,边缘一个缺口,走路咣当作响",
     "wardrobe": "无(本体即服装)", "personality": "慢半拍的老好人", "catchphrase": ""},
]

_ANIME_TAKES = [
    {"logline": f"梗纲{i}:阿丸认真办事,豆包拆台,锅盖慢半拍收场",
     "beats": [f"拍{j}:按爆笑节奏库第{j}拍推进" for j in range(1, 5)],
     "punchline": f"落点{i}:最后一个镜头钉住笑声", "highlight": f"差异点{i}:与前两版玩法不同"}
    for i in range(1, 4)
]

_ANIME_SHOTS = {
    "title": "年夜饭保卫战",
    "shots": [
        {"seq": i, "shot_type": "中景" if i % 2 else "特写",
         "camera": "固定" if i % 3 else "缓推", "duration_s": 5,
         "action_desc": f"第{i}镜:阿丸在灶台前颠勺,围裙沾着面粉,动作干净利落",
         "dialogue": "包在我身上!" if i == 1 else ("看我的!" if i == 6 else ""),
         "speaker": "阿丸" if i == 1 else ("豆包" if i == 6 else ""),
         "characters": ["阿丸"] if i % 2 else ["阿丸", "豆包"],
         "sfx": "锅铲刮底声" if i == 3 else ""}
        for i in range(1, 13)  # 12 镜 × 5s = 60s,贴默认时长档
    ],
}

_ANIME_SHOTCARDS = {"cards": [
    {"seq": i,
     "identity": "白色饭团精灵,蓝围裙" if i % 2 else "黄豆与饭团精灵",
     "card_cn": f"第{i}镜:阿丸双手握铲翻锅,手腕一压一挑,眉毛拧紧又舒展。"
                f"{'中景' if i % 2 else '特写'},机位齐灶台,{'缓推' if i % 3 == 0 else '固定'}。"
                f"暖黄顶光,蒸汽逆光。Q版二头身漫画风。",
     "motion_cn": "颠勺两下,蒸汽涌起,眼神从专注到得意。",
     "voice": "尖着嗓子,语速快" if i == 1 else "",
     "avoid": "蒸汽别糊脸" if i == 3 else ""}
    for i in range(1, 13)
]}

_ANIME_FILM_REPLY = (
    "【第1段|0—15秒】Q版二头身漫画风,线条圆润、平涂上色带软渐变。厨房全景,暖黄灯光,"
    "灶台上蒸汽袅袅。阿丸(白色饭团精灵,蓝色围裙)郑重系紧围裙带子,镜头从围裙特写"
    "缓推拉开到全景,他举起小银勺指向天空:『包在我身上!』——音色亮、语速快、气势十足。"
    "锅铲落锅的脆响压在动作落点上。衔接:硬切。\n"
    "【第2段|15—30秒】同厨房。豆包(黄色小黄豆,红色斗篷)踮脚偷吃主菜,菜量肉眼可见变小,"
    "阿丸背对切菜毫无察觉,视线引导给到锅沿。衔接:延续:本段末帧作下段首帧(图生视频)。\n"
    "【第3段|30—45秒】阿丸额头冒汗,煎蛋下锅,油花四溅成一朵金色烟花;镜头急推到蛋的"
    "特写再拉回全景,蛋比锅还大。衔接:硬切。\n"
    "【第4段|45—60秒】全场举杯,豆包一个嗝把自己崩出画面,只剩红斗篷挂在椅背上,定格。"
    "全片结束"
)


def _feedback_of(prompt: str) -> str:
    """抽出「带话重出」里的用户要求原文(引擎卡/方向提案注入块拼装)。

    引擎卡块写「每张卡」、方向提案块写「每个提案」:统一按「】换行『- 』」兜底截取,
    两个块都能命中(2026-09-24 开书方向提案复用同一提取)。
    """
    j = prompt.find("【用户看了上一批后的修改要求")
    if j >= 0:
        k = prompt.find("\n- ", j)
        if k >= 0:
            return prompt[k + 3:].split("\n", 1)[0].strip()
    # docs/22 方案流:再来三套的带话以 [作者对上一批的反馈: ...] 注入 topic 块
    j = prompt.find("作者对上一批的反馈: ")
    if j >= 0:
        return prompt[j + len("作者对上一批的反馈: "):].split("]", 1)[0].split("\n", 1)[0].strip()
    # docs/22 定向修订:指令在【作者的修改要求】块下一行
    j = prompt.find("【作者的修改要求】")
    if j >= 0:
        return prompt[j + len("【作者的修改要求】"):].strip().split("\n", 1)[0].strip()
    return ""


_BLUEPRINT_DIRECTIVE_MARKER = "【用户对这版蓝图的修改要求(最高优先级,规划每一章时都必须遵守)】\n- "


def _blueprint_text(prompt: str) -> str:
    """按 prompt 里请求的章节约回一段可解析的蓝图文本;带话时在第首章简述里回显要求。"""
    m = (re.search(r"继续生成第\s*(\d+)\s*章到第\s*(\d+)\s*章", prompt)
         or re.search(r"第\s*1\s*章到第\s*(\d+)\s*章", prompt))
    if m:
        start = int(m.group(1)) if m.lastindex and m.lastindex >= 2 else 1
        end = int(m.group(m.lastindex))
    else:
        start, end = 1, 10
    fb = ""
    i = prompt.find(_BLUEPRINT_DIRECTIVE_MARKER)
    if i >= 0:
        fb = prompt[i + len(_BLUEPRINT_DIRECTIVE_MARKER):].split("\n", 1)[0].strip()
    rows = []
    for n in range(start, end + 1):
        summary = f"测试蓝图{n}:推进调查,发现新的线索。"
        if fb and n == start:
            summary = f"【已按你的要求:{fb}】{summary}"
        rows.append(f"第{n}章 - 测试蓝图{n}")
        rows.append("本章定位:推进")
        rows.append(f"本章简述:{summary}")
        rows.append("本章节拍:起|承|合")
    return "\n".join(rows)


def reply_for(prompt: str) -> str:
    # ---- 小说章节链(草稿/定稿/主审/校对/抽取/契约):草稿 prompt 含「第N章」字样,
    # 必须排在蓝图分支之前,否则被 _blueprint_text 截胡、正文为空、审校全降级 ----
    if "请写出第" in prompt and "完整正文" in prompt:
        return (
            "柳三娘把最后一枚铜钱拍在柜台上,扭头就走。\n\n"
            "「站住。」盐商的账房拦在门口,「柳姑娘,这趟镖你接了就得送到。」\n"
            "她回头一笑:「送到可以,开箱验货。」\n"
            "账房脸色一变:「镖规第一条,不问来路,不开箱。」\n"
            "「那就别怪我不讲规矩。」柳三娘袖子一抖,钥匙串叮当作响——"
            "那把黄铜钥匙,正是昨夜她从盐商别院顺出来的。\n"
            "账房盯着钥匙,瞳孔缩成一点:「你……你是『空手柳』?」\n"
            "码头方向忽然传来马蹄声。柳三娘吹了声口哨,压低帽檐:"
            "「开箱吧。让诸位看看,这口棺材里装的到底是什么货。」"
        )
    if "请修订出定稿" in prompt or "修订出定稿" in prompt:
        return (
            "柳三娘把最后一枚铜钱拍在柜台上,扭头就走。\n\n"
            "「站住。」账房拦在门口,「这趟镖,接了就得送到。」\n"
            "她回头一笑:「送到可以——先开箱验货。」\n"
            "「镖规第一条:不问来路,不开箱。」\n"
            "「那就别怪我不讲规矩。」袖子一抖,黄铜钥匙叮当作响。\n"
            "账房瞳孔缩成一点:「你是……『空手柳』?」\n"
            "码头传来马蹄声。柳三娘吹了声口哨,压低帽檐:"
            "「开箱。让诸位看看这口棺材里装的是什么货。」"
        )
    if "资深网文主编" in prompt and "四个维度打分" in prompt:
        return json.dumps({
            "scores": {"plot": 9, "prose": 8, "pacing": 9, "character": 8},
            "score_reasons": {"plot": "冲突直接进事", "prose": "无套话",
                              "pacing": "章末钩子有力", "character": "声音区分开"},
            "comment": "章末钩子落在具体的台词上,合格。",
            "suggestions": [],
        }, ensure_ascii=False)
    if "出版社校对" in prompt:
        return json.dumps({"issues": []}, ensure_ascii=False)
    if "事实抽取" in prompt or "facts" in prompt[:200]:
        return json.dumps({"facts": [], "relationships": []}, ensure_ascii=False)
    if "章末交接" in prompt or "交接契约" in prompt:
        return json.dumps({"summary": "柳三娘亮出钥匙逼开棺验货", "next_setup": "马蹄声逼近码头"}, ensure_ascii=False)
    # ---- 漫剧工坊链(集规划/剧本/分镜/三轨):这些 prompt 都含【书名】字段,
    # 必须全部排在书名分支之前,否则被书名分支截胡(踩过:切集回书名列表→规划为空)。
    if "切分成漫剧的「集」" in prompt or ("集数规划" in prompt and "hook" in prompt):
        return json.dumps({"episodes": [{
            "title": "开箱见活人", "source_chapters": [1],
            "hook": "棺材铺夜半敲门,柳三娘开箱——箱里坐着个穿嫁衣的活人",
            "recap": "柳三娘贪二十两接怪镖,验货夜发现箱中人,钥匙竟连着自己偷的账",
            "cliffhanger": "箱底传来第二声敲响,而码头方向马蹄声已近",
        }]}, ensure_ascii=False)
    if "竖屏漫剧编剧" in prompt:
        return json.dumps({"synopsis": "柳三娘接怪镖,验货夜开箱见嫁衣女,钥匙连着自己偷的账",
                           "lines": [
                               {"speaker": "旁白", "text": "运河码头的雨下到第三天,柳三娘接下了那口棺材镖。",
                                "action": "雨夜码头,柳三娘与盐商账房交割,棺材抬上船"},
                               {"speaker": "柳三娘", "text": "说好不开箱?那这二十两我退你。",
                                "action": "柳三娘掂着钱袋转身要走,账房急拦"},
                               {"speaker": "账房", "text": "送到边关再开!路上死了算你的!",
                                "action": "账房死死按住棺材盖,指节发白"},
                               {"speaker": "柳三娘", "text": "箱子在抖。",
                                "action": "棺材缝里渗出水痕,柳三娘眯起眼"},
                               {"speaker": "旁白", "text": "箱底传来第二声敲响,而码头方向马蹄声已近。",
                                "action": "柳三娘握紧撬棍,回头望向码头灯火"},
                           ]}, ensure_ascii=False)
    if "漫剧分镜师" in prompt:
        return json.dumps({"shots": [
            {"seq": 1, "scene_name": "雨夜码头", "characters": ["柳三娘"],
             "action_desc": "雨夜码头,柳三娘披蓑衣立在棺材旁,手按箱盖,灯笼光在水面晃。60-80 字的画面描述,具体到雨水从蓑衣边缘滴落的节奏。",
             "shot_type": "中景", "camera": "固定", "dialogue": "箱子在抖。", "duration_s": 2},
            {"seq": 2, "scene_name": "雨夜码头", "characters": ["柳三娘"],
             "action_desc": "特写:棺材缝隙渗出水痕,柳三娘瞳孔收紧,握紧撬棍。雨水顺着撬棍木柄滑到她指缝。",
             "shot_type": "特写", "camera": "推", "dialogue": "", "duration_s": 2},
            {"seq": 3, "scene_name": "雨夜码头", "characters": ["柳三娘"],
             "action_desc": "柳三娘猛地撬起一条缝,灯笼光斜切进箱内,她倒吸一口气后退半步,踩碎一枚铜钱。",
             "shot_type": "近景", "camera": "跟随", "dialogue": "", "duration_s": 2},
            {"seq": 4, "scene_name": "雨夜码头", "characters": ["柳三娘"],
             "action_desc": "码头灯火方向传来马蹄声,柳三娘回望,灯笼在她脸上明暗交替,定格。",
             "shot_type": "全景", "camera": "拉", "dialogue": "", "duration_s": 2},
        ]}, ensure_ascii=False)
    if "漫剧" in prompt and "prompt_cn" in prompt:
        return json.dumps({"shots": [{"seq": 1, "prompt_cn": "柳三娘披蓑衣立于雨夜码头棺材旁,手按箱盖。中景,机位齐人眼,固定镜头。冷蓝主调,灯笼暖光点睛,雨水逆光成丝。国漫厚涂,笔触沉稳。",
                                       "prompt_en": "1girl, rain, dock, coffin, lantern, cold blue palette, thick painting, vertical",
                                       "negative": "文字水印,五官错位,多余肢体,低分辨率,模糊"}]}, ensure_ascii=False)
    # 路由顺序即判别顺序:蓝图/骨架提示词互含对方字样(「铺章节蓝图」/「情节架构」),
    # 用「请求章节约」的特征串判别蓝图,骨架再按 segments 关键字接住
    if re.search(r"继续生成第\s*\d+\s*章|生成第\s*1\s*章到第\s*\d+\s*章", prompt):
        return _blueprint_text(prompt)
    # ---- 动画短剧(设定点子/集点子/卡司/简介聊天/梗纲/分镜/整集分段提示词)----
    if "动画策划" in prompt:
        return json.dumps({"premises": [
            "饭团精灵阿丸的深夜食堂,专门招待加班到变形的点心精",
            "怕水的方块茶壶精在水族馆打工,天天和'漏水'危机斗智斗勇",
            "退休的老扫帚在魔法快递站当学徒,最强扫地魔法专治乱塞包裹",
        ]}, ensure_ascii=False)
    if "「下一集」的点子" in prompt:
        return json.dumps({"premises": [
            "停电夜紧急做蛋糕,阿丸靠萤火虫点心精的光完成翻面绝技",
            "豆包误把跳跳糖倒进汤锅,全场客人跟着气泡节拍跳起舞",
            "锅盖打盹滚进蒸笼,被当成'神秘锅盖侠'在全城传闻里越传越玄",
        ]}, ensure_ascii=False)
    if "动画角色设计总监" in prompt:
        return json.dumps({"cast": _ANIME_CAST}, ensure_ascii=False)
    if "动画编剧搭档" in prompt:
        return json.dumps({
            "reply": "接住了:豆包偷吃主菜,我补了阿丸用一颗蛋救场的反转,结尾让豆包崩出画面。",
            "synopsis": "年夜饭夜,阿丸一本正经立下军令状独力撑起全场年夜饭;豆包趁乱偷吃,"
                        "主菜肉眼可见变小;阿丸发现后不拆穿,反手煎出一颗比锅还大的蛋压住全场;"
                        "收尾全场举杯,豆包一个嗝把自己崩出画面,只剩红斗篷挂在椅背上——"
                        "落点:那件空斗篷的定格。",
        }, ensure_ascii=False)
    if "动画编剧总监" in prompt:
        return json.dumps({"takes": _ANIME_TAKES}, ensure_ascii=False)
    if "动画的分镜师" in prompt:
        return json.dumps(_ANIME_SHOTS, ensure_ascii=False)
    if "镜头卡" in prompt:
        # docs/21 镜头卡工艺:一镜一卡 JSON(引擎拼文档;旧分段式仅在工艺包停用时走)
        return json.dumps(_ANIME_SHOTCARDS, ensure_ascii=False)
    if "动画导演兼提示词工程师" in prompt:
        return _ANIME_FILM_REPLY
    # ---- 系列短片(主角点子/剧情点子/定妆代写/单集提示词)----
    if "竖屏短视频的角色策划" in prompt:
        return json.dumps({"ideas": [
            {"name": "茶壶精", "brief": "怕水的方块茶壶精在水族馆打工,天天和漏水危机斗智斗勇"},
            {"name": "老扫帚", "brief": "退休老扫帚在魔法快递站当学徒,扫地魔法专治乱塞包裹"},
            {"name": "饭团丸", "brief": "饭团精灵开深夜食堂,专招待加班到变形的点心精"},
        ]}, ensure_ascii=False)
    if "「下一集」的剧情点子" in prompt:
        return json.dumps({"plots": [
            "小浣熊盯上货架最上层的蜂蜜罐,踮脚晃罐,一屁股坐地上稳稳接住",
            "收银台抽屉卡住,小浣熊用橡果当垫片修好,顺手多收了一颗小费",
            "打烊后小浣熊给每件商品道晚安,被夜班摄像头拍下成了都市传说",
        ]}, ensure_ascii=False)
    if "角色设计总监" in prompt:
        return json.dumps({"look":
            "一只成年小浣熊,体态圆润敦实,站起来约到成年人膝盖;灰褐色粗毛,眼圈与尾环"
            "深黑,耳尖米白,左耳缺一小口(跨集认脸的记号);常年穿洗旧的红色针织围巾,"
            "右爪总攥着一颗橡果;性格好奇又嘴硬,招牌动作是抱爪眯眼歪头打量。"},
            ensure_ascii=False)
    if "导演兼摄影指导" in prompt:
        return json.dumps({
            "title": "蜂蜜罐保卫战",
            "prompt_cn": "黄昏杂货店,暖黄顶灯。穿红围巾的小浣熊蹲在第二层货架前,"
                         "抱爪眯眼歪头打量最上层的蜂蜜罐;它踮起后爪扒住货架边缘,"
                         "罐子一寸寸挪到边上,脱爪瞬间前爪在空中捞了两把,最后抱着罐子"
                         "瘫坐在地,又警觉地左右看看,把罐子塞进围巾里。环境音:顶灯电流"
                         "嗡鸣、罐子滚过木架的闷响、短促鼻息。",
            "negative": "畸变,多手多脚,文字,水印",
        }, ensure_ascii=False)
    if "先用三个问题把方向定住" in prompt:
        # 开书方案流(docs/22 P0):三问定纲,每问 3 候选 + ★首推带理由
        def _q(key: str, title: str, cands: list) -> dict:
            return {"key": key, "title": title, "candidates": [
                {"text": t, "recommended": i == 0, "reason": r}
                for i, (t, r) in enumerate(cands)
            ]}
        short = "结尾想落在什么感觉" in prompt
        avoid = "避开清单" in prompt
        if avoid:
            # 「🎲换一批」防趋同:mock 换 B 组候选(角度/人群/张力全换),供走查断言
            return json.dumps({"questions": [
                _q("q1", "写什么味道", [
                    ("东方奇幻·诡谲瑰丽", "避开上一批的现实向,开奇幻位面"),
                    ("历史权谋·苍凉厚重", "换个时代换群人"),
                    ("武侠诡事·侠气森然", "江湖夜雨,快意与谜"),
                ]),
                _q("q2", "主角是谁", [
                    ("刻薄账房先生,算无遗策却算不透人心", "与上一批的体制内女警完全换人"),
                    ("哑女刀客,以刀代言", "沉默型主角,张力在刀上"),
                    ("过气影帝,戏里戏外分不清", "职业反差新颖"),
                ]),
                _q("q3", "结尾想落在什么感觉上" if short else "最大的坎是什么", [
                    ("苍凉·回甘" if short else "至亲即是局中人", "情感钩最深"),
                    ("荒诞·大笑" if short else "天道本身在撒谎", "立意反转"),
                    ("温柔·释怀" if short else "救命恩人是仇人", "撕裂感最强"),
                ]),
            ]}, ensure_ascii=False)
        return json.dumps({"questions": [
            _q("q1", "写什么味道", [
                ("都市异闻·冷峻悬疑", "贴你给的题材,悬念密度最高"),
                ("都市温情·治愈日常", "反差候选,暖着来"),
                ("都市黑幕·冷硬写实", "冲突更狠,后劲大"),
            ]),
            _q("q2", "主角是谁", [
                ("口吃档案员女警,过目不忘却无人信", "反差最强,憋屈感自带引擎"),
                ("跑单王骑手,市井江湖气", "接地气,情绪共鸣快"),
                ("失眠电台主持,对声音变态敏感", "题材新颖,氛围独"),
            ]),
            _q("q3", "结尾想落在什么感觉上" if short else "最大的坎是什么", [
                ("释然·微光" if short else "泄密者就在身边",
                 "短故事收在情绪上" if short else "内部敌人张力最大"),
                ("酸楚·余味" if short else "体制本身是共谋", "后劲长,回味久"),
                ("灼热·滚烫" if short else "真凶偏偏是恩人", "情感撕裂最狠"),
            ]),
        ]}, ensure_ascii=False)
    if "只按修改要求修订" in prompt:
        # 定向修订(docs/22 P0):一句话只改这一套——mock 固定改 protagonist 并回显要求
        fb = _feedback_of(prompt) or "你的要求"
        return json.dumps({
            "title": "死人镖", "kernel": "替死人讨公道的瘸腿老镖师,押着装活人的棺材去边关",
            "protagonist": f"赵铁衣,{fb}(已按你的一句话修订)",
            "world": "架空大梁朝末年,低武写实,只有刀法暗器和人心算计",
            "arc": "接暗镖开箱见活人;十五天活着送到边关;卷尾揭当年死镖真相",
            "engine": "每一站都是一场验货式反转", "flavor": ["硬派江湖", "冷冽"],
            "scale": "长篇", "scale_reason": "七城七案,值得铺", "label": "老镖师·旧案·冷",
        }, ensure_ascii=False)
    if "3 套完整的「短故事方案」" in prompt:
        return json.dumps({"plans": [
            {"title": "最后一课", "kernel": "代课老师用最后一节课送走想辍学的学生",
             "protagonist": "陈默,58岁,想体面退场;嘴硬心软",
             "world": "县城中学,冬天,教室后墙贴着褪色的奖状",
             "arc": "开端在作业堆里发现辍学信;转折家访撞见真相;结尾空座位上放着一封没寄出的信",
             "ending": "怅然·微光", "flavor": ["温情"], "scale": "8千字",
             "scale_reason": "单一事件一次讲透", "label": "老师·挽留·温情"},
            {"title": "夜班公交", "kernel": "末班车司机每晚多等一个不存在的乘客",
             "protagonist": "老周,50岁,寡言;方向盘磨得发亮",
             "world": "城市深夜,末班车,车厢空得能听见报站声",
             "arc": "开端末班总多一人;转折揭出是亡妻;结尾空站台灯还亮着",
             "ending": "酸楚·释然", "flavor": ["都市传说"], "scale": "3千字",
             "scale_reason": "一个反转就够", "label": "司机·执念·传说"},
        ]}, ensure_ascii=False)
    if "一次给出 3 套完整的「整书方案」" in prompt:
        # 整书方案×3(docs/22 P0):方案一直读作者想法,另两套发散;差异轴拉开
        fb = _feedback_of(prompt)
        avoid = "避开清单" in prompt
        if avoid:
            # 防趋同:mock 换 B 组三套(主角身份/冲突来源/味道全换),供走查断言
            plans = [
                {"title": "漕河账", "kernel": "漕帮账房先生用一本暗账搅动三省盐铁",
                 "protagonist": "秦九思,四十四岁,算无遗策却算不透人心",
                 "world": "清中期漕运盛景,江湖与官面在码头交汇",
                 "arc": "暗账失窃;三省追账;卷尾账主竟是自己",
                 "engine": "每翻一页账就倒一个人",
                 "flavor": ["权谋", "苍凉"], "scale": "长篇",
                 "scale_reason": "三省三案", "label": "账房·暗账·权"},
                {"title": "哑刀", "kernel": "哑女刀客替村庄讨还十年前的血债",
                 "protagonist": "阿盐,十九岁,以刀代言",
                 "world": "塞北边镇,刀是唯一的法律",
                 "arc": "血债现形;寻仇北上;卷尾仇人是师父",
                 "engine": "每一站一个债主",
                 "flavor": ["武侠", "冷冽"], "scale": "中篇",
                 "scale_reason": "单线复仇", "label": "刀客·血债·冷"},
                {"title": "谢幕", "kernel": "过气影帝在戏里戏外间追查一场旧案的真相",
                 "protagonist": "陆沉,五十岁,戏里戏外分不清",
                 "world": "当代影视圈,镜头是照妖镜",
                 "arc": "旧案入戏;戏假成真;卷尾导演喊咔后无人下场",
                 "engine": "每部戏一层真相",
                 "flavor": ["悬疑", "荒诞"], "scale": "长篇",
                 "scale_reason": "戏中案连环", "label": "影帝·旧案·诡"},
            ]
            if fb:
                plans[0]["kernel"] = f"按你的要求重出:『{fb}』——" + plans[0]["kernel"]
            return json.dumps({"plans": plans}, ensure_ascii=False)
        plans = [
            {"title": "死人镖",
             "kernel": "替死人讨公道的瘸腿老镖师,押着装活人的棺材去边关,靠一本黑账把七座城的贪官全拖下水",
             "protagonist": "赵铁衣,五十二岁,威远镖局总镖头出身,断过左腿;想赎回祖宅,更想知道当年谁出卖了兄弟;认死理,欠命还一辈子",
             "world": "架空大梁朝末年,官道匪患交织;低武写实——只有刀法暗器毒药和人心算计",
             "arc": "接暗镖开箱发现被捆的年轻女子;十五天内活着送到边关,边军县令同行三方追杀;卷尾女子说出当年死镖的真相",
             "engine": "每一站都是一场验货式反转,读者追下一个开箱开出什么",
             "flavor": ["硬派江湖", "公路悬疑"], "scale": "长篇",
             "scale_reason": "七城七案,值得铺", "label": "老镖师·旧案·冷"},
            {"title": "箱中娇",
             "kernel": "只想赚够棺材本跑路的市井女骗子,被迫押着装活人的镖箱,在盐商水匪之间反复横跳",
             "protagonist": "柳三娘,二十七岁,账房丫头出身,一身假身份混饭;想赚一百两去岭南开茶摊;精明怕死,但见不得女人被当货物",
             "world": "架空南陈朝,运河盐铁走私猖獗;规则是谁掌握假路引假账本谁穿行黑白两道",
             "arc": "贪二十两接怪镖开箱见嫁衣盐商女;想扔箱跑却发现钥匙连着自己偷的那箱真账;芦苇荡里箱底还有第三个人",
             "engine": "假身份套假身份的骗中骗,每码头换一层皮",
             "flavor": ["市井狡黠", "黑色幽默"], "scale": "中篇",
             "scale_reason": "单线骗局节奏快", "label": "女骗子·骗局·趣"},
            {"title": "失眠者电台",
             "kernel": "重度失眠的深夜电台主持,陷进听众来电预告的真实谋杀,靠对声音的敏感反杀真凶",
             "protagonist": "苏晚,三十一岁,靠药物才能睡两小时;想睡个整觉,不想再被台里边缘化;温柔的偏执",
             "world": "当代都市,广播被短视频挤压;直播间是最后几个匿名倾诉的公共空间",
             "arc": "来电预告三天后的谋杀居然字字应验;直播间变成预告杀人秀场,警方怀疑她炒作;卷尾她从背景音听出凶手就在电台大楼里",
             "engine": "每期节目一场声音猫鼠,读者听声猜凶",
             "flavor": ["心理惊悚", "声音美学"], "scale": "连载",
             "scale_reason": "单元案可无限续", "label": "主持·声音·惊悚"},
        ]
        if fb:
            plans[0]["kernel"] = f"按你的要求重出:『{fb}』——" + plans[0]["kernel"]
        return json.dumps({"plans": plans}, ensure_ascii=False)
    if "可以拍板的「开书订单」" in prompt:
        # 开书对话式确认流(L0):单次调用回 reply + 当前开书订单(五段)草稿
        return json.dumps({
            "reply": "接住了。主角先用李镖头这个方向,困境按「护送活的违禁品」来聊——"
                     "世界观底盘你想要哪一种:①乱世江湖,纯现实无超自然 ②末法修仙,"
                     "灵气枯竭 ③架空王朝,有低武体系?",
            "brief": "【故事内核】落魄镖师接下退休前最后一趟不许开箱的险镖,验货夜发现"
                     "箱中藏着通缉的前朝公主\n【主角】李镖头,四十岁,想金盆洗手却被"
                     "最后一趟镖锁死;嘴硬心软,规矩比命重\n【困境与破局】江湖规矩要"
                     "她死,他的良心要她活;破局靠一身老底子的镖行绝活和三个老兄弟\n"
                     "【世界观底盘】未定(候选:乱世江湖纯现实/末法修仙/架空低武)\n"
                     "【味道与连载引擎】冷峻里带热血;每趟镖一个新局,良心问价层层加码",
        }, ensure_ascii=False)
    if "个方向提案供TA挑" in prompt:
        # 🎲 点子兜底:三个 100-150 字方向提案(差异轴拉开;带话时首条回显要求)
        cards = [
            {"pitch": "落魄镖师接下退休前最后一趟不许开箱的险镖,验货夜发现箱中藏着通缉的"
                      "前朝公主;江湖规矩要她死,他的良心要她活,靠一身老底子的镖行绝活"
                      "护她过关。冷峻里带热血,每趟镖一个新局。", "label": "小人物·生计·燃"},
            {"pitch": "退休刑警整理旧档案,发现自己经手的悬案全是同一只手布置的冤案;"
                      "警局不认,受害者家属早已放弃,他带着老花镜和旧卷宗一个个重查。"
                      "冷调悬疑,自我怀疑是最大的敌人。", "label": "老手·立场·冷"},
            {"pitch": "外卖骑手在高架桥下捡到一部只能拨通「一年后自己」的手机,电话那头"
                      "警告他今晚别接某单;他偏接了,从此每单都在和一年后的自己抢时间。"
                      "都市奇幻,爽感带悬念。", "label": "骑手·奇幻·爽"},
        ]
        fb = _feedback_of(prompt)
        if fb:
            cards[0]["pitch"] = f"按你的要求重出:『{fb}』——小人物卷入风波,绝活破局," \
                                "具体的困境与反差见对谈;冷峻带热血,每趟一个新局。"
            cards[0]["label"] = f"已带要求·{cards[0]['label']}"
        return json.dumps({"pitches": cards}, ensure_ascii=False)
    if "深化成一个完整的故事概念" in prompt:
        return json.dumps(_CONCEPT, ensure_ascii=False)
    if "产出一批「故事引擎卡」" in prompt:
        cards = [dict(c) for c in _ENGINE_CARDS]
        fb = _feedback_of(prompt)
        if fb:
            cards[0]["engine"] = f"按你的要求重出:『{fb}』——小人物卷入风波,绝活破局"
            cards[0]["hook"] = f"已带上你的要求:{fb}"
        return json.dumps({"engines": cards}, ensure_ascii=False)
    if "正在打磨的故事概念" in prompt:
        # 带话精修概念(确认链 L1 概念打磨房):回 changed 字段供前端 diff 高亮
        c = dict(_CONCEPT)
        c["logline"] = "按你的要求改过:" + _CONCEPT["logline"]
        return json.dumps(
            {"concept": c, "changed": ["logline"], "note": "已按你的要求重捏一句话故事。"},
            ensure_ascii=False)
    if "扩展出" in prompt and "故事概念" in prompt:
        ideas = []
        for i in range(4):
            c = dict(_CONCEPT)
            c["logline"] = f"方案{i + 1}:" + _CONCEPT["logline"]
            ideas.append(c)
        return json.dumps(
            {"ideas": ideas, "comparison": "四个方案同源不同切入,按偏好挑即可。"},
            ensure_ascii=False)
    if "候选大类" in prompt:
        return json.dumps({"category": "", "genre": "", "suggestions": []}, ensure_ascii=False)
    if "核心梗卡" in prompt:
        return json.dumps({
            "high_concept": "镖箱里藏着个大活人,不问来路的镖规成了催命符",
            "payoff": "每开一次箱就欠一分债,债主逐级抬升,逼镖头在规矩与良心间反复下注",
            "beats": ["接镖", "异响", "开箱", "追杀", "摊牌"],
            "boundaries": ["活人身份不得提前泄露", "镖头不得无故弃镖"],
            "hook_plan": {"opening": "箱中传来敲击声", "mid": "活人是仇家之女", "climax": "雇主亲自截镖"},
        }, ensure_ascii=False)
    if '"segments"' in prompt or "故事骨架" in prompt:
        return json.dumps({"segments": [
            {"title": "接镖入局", "goal": "从接下险镖到发现镖箱有异,瞒不住的裂缝初现。",
             "conflict": "镖规不许开箱 vs 好奇与良心", "start_state": "落魄接镖", "end_state": "深夜异响"},
            {"title": "开箱惊变", "goal": "开箱见活人,亡命千里,追杀与真相同步逼近。",
             "conflict": "保镖 vs 追杀方", "start_state": "深夜异响", "end_state": "身份揭穿"},
            {"title": "真相反杀", "goal": "雇主真面目揭开,镖头反杀定局,带着人与债走向新生。",
             "conflict": "最终摊牌", "start_state": "身份揭穿", "end_state": "尘埃落定"},
        ]}, ensure_ascii=False)
    if "动力学模型" in prompt:
        return "李镖头(主角):金盆洗手不得,押镖入了死局。弧光:从求稳到担责。"
    if "全书情节架构" in prompt:
        return "第一幕(1-10 章)接镖入局;第二幕(11-20 章)开箱惊变、亡命千里;第三幕(21-30 章)真相反杀。"
    if "三维交织的世界观" in prompt:
        return "乱世末年,镖局行业凋零,江湖规矩凌驾王法。"
    if "第一步构建故事核心" in prompt:
        return "押镖人发现自己运的不是货,是命。"
    if "书名" in prompt:
        return "活人镖\n镖人重启\n暗镖\n不可开箱\n千里验货人"
    return "第1章 - 风雪夜验镖\n第2章 - 开箱见活人"


@app.post("/v1/chat/completions")
async def chat(body: dict):
    prompt = "".join(
        (m.get("content") or "") for m in body.get("messages", []) if isinstance(m, dict)
    )
    # 注入探针(docs/23 走查):确认 skill 包条目真的进了 prompt,打印到日志供断言
    if "创作 Skill" in prompt:
        hit = [k for k in ("爽点循环", "对话占比过半", "四件套", "爽点兑现") if k in prompt]
        print(f"[skill-probe] 注入命中: {hit} | prompt 长度: {len(prompt)}", flush=True)
    text = reply_for(prompt)
    if not body.get("stream"):
        return JSONResponse({
            "id": "mock", "object": "chat.completion", "model": "mock-1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })

    async def sse():
        for i in range(0, len(text), 24):
            yield "data: " + json.dumps({
                "id": "mock", "object": "chat.completion.chunk", "model": "mock-1",
                "choices": [{"index": 0, "delta": {"content": text[i:i + 24]},
                             "finish_reason": None}],
            }, ensure_ascii=False) + "\n\n"
            if STREAM_DELAY > 0:
                await asyncio.sleep(STREAM_DELAY)
        yield "data: " + json.dumps({
            "id": "mock", "object": "chat.completion.chunk", "model": "mock-1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }) + "\n\ndata: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-1", "object": "model"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
