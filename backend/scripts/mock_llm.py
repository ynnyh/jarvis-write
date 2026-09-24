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
    if j < 0:
        return ""
    k = prompt.find("\n- ", j)
    if k < 0:
        return ""
    return prompt[k + 3:].split("\n", 1)[0].strip()


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
