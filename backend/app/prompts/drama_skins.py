# app/prompts/drama_skins.py
# -*- coding: utf-8 -*-
"""漫剧源书的频道皮肤目录(docs/23 v2,2026-09-26 作者实测反馈落地)。

爽文双包的 idea 条目里有一份同款清单(给 LLM 的兜底货架);本目录是它的
**结构化镜像**,给前端想法屏的「皮肤墙」渲染用——用户选完频道直接看到
对应标签点选,而不是掉进通用小说开书流。两处清单保持同步:皮肤风向轮动
时两处一起改(改这里+改包条目,都不用动交互代码)。
"""
from __future__ import annotations

# 每条:key(稳定键)/label(墙上的标签,与包条目清单逐字一致)/desc(一句话爽点侧重)
DRAMA_SKINS: dict[str, list[dict]] = {
    "male": [
        {"key": "pig_eating_tiger", "label": "扮猪吃虎", "desc": "藏拙的强者被低估,亮底牌碾压全场"},
        {"key": "system", "label": "系统流", "desc": "金手指系统派任务,成长看得见摸得着"},
        {"key": "xianxia", "label": "玄幻修仙", "desc": "境界体系步步登天,宗门恩怨打脸不断"},
        {"key": "mountain_hunt", "label": "赶山狩猎", "desc": "深山老林寻宝收获,一年四季都有惊喜"},
        {"key": "farming", "label": "种田经营", "desc": "从小摊到产业,经营版图越滚越大"},
        {"key": "apocalypse", "label": "末世求生", "desc": "囤物资建据点,乱世里步步为营"},
        {"key": "urban_warlord", "label": "都市战神", "desc": "蛰伏大佬回归都市,一句话摆平一切"},
        {"key": "workplace", "label": "职场打脸", "desc": "被看轻的实干者,用成绩回敬所有质疑"},
        {"key": "historical", "label": "历史权谋", "desc": "寒门崛起步步为营,朝堂之上翻云覆雨"},
        {"key": "beast_taming", "label": "高武御兽", "desc": "契约兽宠收集养成,战力天花板一路抬"},
    ],
    "female": [
        {"key": "grand_revenge", "label": "大女主复仇", "desc": "不靠拯救者,自己一步步掀翻算计"},
        {"key": "rebirth", "label": "重生虐渣", "desc": "带着记忆重来,渣男贱女逐一清算"},
        {"key": "wealthy_clan", "label": "豪门恩怨", "desc": "真假千金/豪门认亲,身份反转打脸"},
        {"key": "hidden_boss", "label": "马甲大佬", "desc": "低调主角马甲一层层揭,惊掉所有人下巴"},
        {"key": "chase_wife", "label": "追妻火葬场", "desc": "前期错付虐心,后期追悔莫及"},
        {"key": "era_space", "label": "年代空间", "desc": "穿回年代带金手指,缺衣少食也能活得滋润"},
        {"key": "house_feud", "label": "宅斗宫斗", "desc": "嫡女翻身步步为营,后宅深宫全是战场"},
        {"key": "marriage_first", "label": "先婚后爱", "desc": "从契约开始的感情,慢慢处出真心"},
        {"key": "time_travel_farm", "label": "穿越种田", "desc": "古代白手起家,小日子经营得风生水起"},
        {"key": "cute_baby", "label": "萌宝逆袭", "desc": "带娃逆袭,萌宝助攻掀翻前尘旧局"},
    ],
}


def skins_of(audience: str) -> list[dict]:
    """频道 → 皮肤清单;不认识的频道回空清单(前端按空态处理)。"""
    return DRAMA_SKINS.get(audience, [])
