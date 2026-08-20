# app/engines/drift/contracts.py
# -*- coding: utf-8 -*-
"""题材契约:题材漂移治理的单一真相源。

两块数据:
1. FORBIDDEN_ELEMENTS 禁忌元素注册表:现实向被网文惯性带偏的高频「非现实」入侵
   (超能力/系统/穿越/觉醒/重生/修仙/魔法/无限流/灵异…)。每条带识别正则 + 适用模式。
   设计原则:正则走「高召回」(宁可多框),精度交给 self_heal 的 LLM-judge 去误报——
   这正是用户拍板的「硬门:regex 预筛 + LLM 确认 + 越线毙+重生」。
2. GENRE_CONTRACTS 题材契约:每个题材分类 → 题材模式 + 必须有 + 味道基线。

模式(mode)决定禁忌是否生效:
- realistic 现实向:全部禁忌生效(青春校园/历史/军事/社会派悬疑)。
- fantasy   幻想向:这些元素多为题材本体(玄幻的觉醒、游戏的系统、仙侠的修真),不设禁。
- mixed     混合向:默认不设模式级禁忌,只认用户自定义 must_not。

用户可覆盖:在 DNA.must 里明确写了某禁忌元素 = 明确要它 → 该元素放行(见 effective_patterns)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ForbiddenElement:
    """一个「非现实」入侵元素。pattern 高召回,modes 指其在哪些模式下算越界。"""

    key: str
    label: str                      # 人类可读名(前端展示 / judge 用)
    pattern: str                    # 识别正则(高召回,配合 LLM-judge 去误报)
    modes: tuple[str, ...] = ("realistic",)


# 现实向的「非现实」入侵清单。pattern 尽量要求「幻想宾语」以压低误报
# (如「觉醒」必须后接 异能/血脉 等,避免误伤「觉醒了对文学的热爱」)。
FORBIDDEN_ELEMENTS: list[ForbiddenElement] = [
    ForbiddenElement(
        "superpower", "超能力/异能",
        r"超能力|异能力?|特异功能|超凡之力|变异(?:觉醒|能力)|读心术|瞬间移动",
    ),
    ForbiddenElement(
        "awaken", "金手指式觉醒",
        r"觉醒(?:了)?(?:异能|超能力?|能力|血脉|天赋|系统|金手指|神通|前世记忆)",
    ),
    ForbiddenElement(
        "system", "系统/面板/金手指",
        r"(?:绑定|激活|开启)?系统(?:面板|提示|奖励|商城|签到|任务|流)?"
        r"|金手指|签到(?:系统|领取|奖励)|状态栏|属性面板|随身空间|作弊器",
    ),
    ForbiddenElement(
        "rebirth", "重生/穿越",
        r"重生(?:归来|一世|重来)?|穿越(?:到|回|成)|魂穿|夺舍|异世(?:大陆|界)"
        r"|回到(?:过去|古代|前世)",
    ),
    ForbiddenElement(
        "cultivation", "修仙/修真",
        r"修仙|修真|渡劫|结丹|元婴|筑基|金丹|灵气复苏|吸收灵气|真气|内力(?:暴涨|突破)?|飞升",
    ),
    ForbiddenElement(
        "magic", "魔法/斗气/异族",
        r"魔法(?:阵|师)?|斗气|魔力|法术|魔兽|精灵族|兽人族|龙族|巫师|结界",
    ),
    ForbiddenElement(
        "infinite", "无限流/副本",
        r"无限流|(?:进入|通关)副本|轮回空间|主神空间|通关奖励|恐怖复苏",
    ),
    ForbiddenElement(
        "supernatural", "灵异/鬼怪",
        r"鬼魂|僵尸|丧尸|吸血鬼|阴兵|通灵|附身|养鬼|镇魂|阴阳眼",
    ),
]


@dataclass(frozen=True)
class GenreContract:
    """一个题材分类的契约:题材模式 + 必须有 + 味道基线。"""

    category: str
    mode: str                       # realistic / fantasy / mixed
    must: tuple[str, ...]           # 该题材「必须有」的看点/质感(提示,可空)
    taste: str                      # 味道基线(一句话,给用户与蒸馏参照)


# 11 个题材分类的契约(category key 对齐 config/tag_presets.json 的 genre.categories)。
GENRE_CONTRACTS: dict[str, GenreContract] = {
    "realistic": GenreContract(
        "realistic", "realistic",
        ("现实逻辑与生活质感", "来自现实的冲突(人际/家庭/成长/时代)"),
        "扎根现实:冲突与情感都在现实规则内展开,靠真实细节而非超自然设定打动人。",
    ),
    "romance": GenreContract(
        "romance", "mixed",
        ("清晰的情感线与关系推进", "有张力的互动与心理"),
        "情感为核,关系推进有节奏;甜或虐都要有代入感,别靠狗血硬拗。",
    ),
    "urban": GenreContract(
        "urban", "mixed",
        ("当代都市背景", "贴近现实的生活/职场/人情质感"),
        "都市烟火气,节奏明快;若定为纯现实向,则不引入异能/系统/重生。",
    ),
    "history": GenreContract(
        "history", "realistic",
        ("时代考据感", "个人命运与大势交织"),
        "厚重考据,个人扣在时代上;非穿越向不用现代梗与金手指。",
    ),
    "military": GenreContract(
        "military", "realistic",
        ("可信的专业细节", "智斗、信息差与信念"),
        "冷硬专业,靠意志与博弈推进,不开外挂。",
    ),
    "mystery": GenreContract(
        "mystery", "mixed",
        ("谜面与线索", "公平的解谜与反转"),
        "氛围压抑、逻辑严密;社会派现实向不靠灵异解题,灵异向另论。",
    ),
    "wuxia": GenreContract(
        "wuxia", "fantasy",
        ("武功与江湖", "侠义、恩怨与人情"),
        "古典白话的江湖气,武功为低幻想,重侠义人情而非无脑升级。",
    ),
    "xuanhuan": GenreContract(
        "xuanhuan", "fantasy",
        ("自洽的力量/境界体系", "突破、奇遇与格局升级"),
        "东方玄幻的爽感升级:体系自洽、格局渐大,爽要有铺垫和代价。",
    ),
    "xianxia": GenreContract(
        "xianxia", "fantasy",
        ("修行体系与仙道", "机缘、劫难与道心"),
        "仙侠的缥缈与残酷并存,修行有代价,不是纯打怪升级。",
    ),
    "scifi": GenreContract(
        "scifi", "fantasy",
        ("自洽的科幻设定", "点子、思辨与推演"),
        "设定驱动、逻辑自洽,震撼来自推演而非拍脑袋的奇观。",
    ),
    "game": GenreContract(
        "game", "fantasy",
        ("游戏/竞技规则", "成长、团队与热血"),
        "游戏或竞技的规则感与热血;系统/数值在此题材内合法。",
    ),
}


def contract_for(category: str) -> GenreContract | None:
    """按题材分类 key 取契约;未知/空返回 None。"""
    return GENRE_CONTRACTS.get((category or "").strip())


def mode_for_category(category: str) -> str:
    """题材分类 → 建议题材模式;未知返回空串(不启用模式级硬门)。"""
    c = contract_for(category)
    return c.mode if c else ""


def forbidden_for_mode(mode: str) -> list[ForbiddenElement]:
    """该模式下生效的全部禁忌元素(mixed/空模式 → 无模式级禁忌)。"""
    mode = (mode or "").strip()
    if not mode:
        return []
    return [e for e in FORBIDDEN_ELEMENTS if mode in e.modes]


def forbidden_labels_for_mode(mode: str) -> list[str]:
    """该模式下的禁忌元素名列表(供坐标卡预填 DNA.must_not、品味镜展示)。"""
    return [e.label for e in forbidden_for_mode(mode)]


def _opted_in(label: str, opt_in: tuple[str, ...]) -> bool:
    """用户是否在 must 里明确要了该禁忌元素(明确要 = 放行,尊重「除非用户明确要求」)。"""
    head = label.split("/")[0]
    for o in opt_in:
        o = (o or "").strip()
        if o and (o in label or head in o or o in head):
            return True
    return False


def effective_patterns(
    mode: str,
    extra_must_not: tuple[str, ...] = (),
    opt_in: tuple[str, ...] = (),
) -> list[tuple[str, "re.Pattern[str]"]]:
    """本次生效的禁忌识别集:(label, 已编译正则)。

    = 模式级禁忌(减去用户在 must 里明确点名要的)+ 用户自定义 must_not(按字面匹配)。
    """
    items: list[tuple[str, re.Pattern[str]]] = []
    for e in forbidden_for_mode(mode):
        if _opted_in(e.label, opt_in):
            continue
        items.append((e.label, re.compile(e.pattern)))
    seen = {lbl for lbl, _ in items}
    for m in extra_must_not:
        m = (m or "").strip()
        if m and m not in seen:
            items.append((m, re.compile(re.escape(m))))
            seen.add(m)
    return items
