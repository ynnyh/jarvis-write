# app/engines/drama/gender.py
# -*- coding: utf-8 -*-
"""角色性别:判定 / 下发提示词 / 事后校验。

为什么值得一个模块:性别写错是漫剧里最刺眼的错——女角色出成男相,观众一眼看出,
而且错误会顺着「锁定外貌段 → 定妆照提示词 → 每格分镜提示词 → 配音声线」一路复制,
整片人物全废。修它要串起四层,判定逻辑集中在一处才不会各处各写一套。

判定只吃「档案里真有的线索」(代词/称谓/身份词),**不靠名字算命**:
中文名判性别错得离谱(「李默」既可男可女),宁可判「未定」交用户拍板,
也不替他猜——猜错比不猜更难发现。
"""
from __future__ import annotations

import re

# ""=未定(交用户拍板)/female/male/other(非二元或刻意不明确)
VALID_GENDERS = ("", "female", "male", "other")

_LABELS = {"female": "女", "male": "男", "other": "非二元/不明确"}

_ALIASES = {
    "female": ("female", "f", "woman", "女", "女性", "女生", "女的", "女子"),
    "male": ("male", "m", "man", "男", "男性", "男生", "男的", "男子"),
    "other": ("other", "nonbinary", "non-binary", "其他", "非二元", "不明确", "无性别"),
}


def normalize_gender(raw: object) -> str:
    """把用户/模型给的各种写法收敛成四个合法值之一;不认识的一律当「未定」。"""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    for g, words in _ALIASES.items():
        if s in words:
            return g
    return ""


def gender_label(gender: object) -> str:
    """人话标签:女/男/非二元;未定返回空串。"""
    return _LABELS.get(normalize_gender(gender), "")


def gender_tag(gender: object) -> str:
    """提示词里的短标注「性别:女」;未定返回空串(不写反而不会误导模型)。"""
    label = gender_label(gender)
    return f"性别:{label}" if label else ""


def gender_paren(gender: object) -> str:
    """注入画面提示词的括号后缀「(女性)」——给生图站一个躲不开的性别锚。"""
    g = normalize_gender(gender)
    return f"({_LABELS[g]}性)" if g in ("female", "male") else ""


def gender_phrase_cn(gender: object) -> str:
    g = normalize_gender(gender)
    return f"{_LABELS[g]}性角色" if g in ("female", "male") else ""


def gender_phrase_en(gender: object) -> str:
    g = normalize_gender(gender)
    return {"female": "female character", "male": "male character"}.get(g, "")


def gender_directive(gender: object, evidence: str = "") -> str:
    """角色档案行里的性别那一段:定了就说硬约束,没定就要模型自己判断并回填。"""
    g = normalize_gender(gender)
    if g in ("female", "male"):
        why = f",依据:{evidence}" if evidence else ""
        return f"性别:{_LABELS[g]}【硬约束,不许改{why}】"
    if g == "other":
        return "性别:非二元/刻意不明确【别写成典型男相或女相】"
    return "性别:未判明【你按档案线索判断,并把结论写进 gender 字段】"


# =============== 从档案文本判性别 ===============
# 权重:代词与直接性别词最硬(3),身份称谓次之(2),单字兜底最弱(1)。
# 单字「女/男」放最后:「女扮男装」这类会一比一抵消,判成未定 —— 宁可不判。
_EVIDENCE: dict[str, tuple[str, int]] = {}
for _w, _n in (
    ("她", 3), ("女子", 3), ("女性", 3), ("女孩", 3), ("少女", 3), ("姑娘", 3),
    ("女人", 3), ("妇人", 3), ("母亲", 3), ("妻子", 3), ("夫人", 3), ("女儿", 3),
    ("小姐", 2), ("娘子", 2), ("公主", 2), ("皇后", 2), ("王妃", 2), ("太后", 2),
    ("师姐", 2), ("师妹", 2), ("姐姐", 2), ("妹妹", 2), ("女侠", 2), ("女帝", 2),
    ("女", 1),
):
    _EVIDENCE[_w] = ("female", _n)
for _w, _n in (
    ("他", 3), ("男子", 3), ("男性", 3), ("男孩", 3), ("少年", 3), ("男人", 3),
    ("父亲", 3), ("丈夫", 3), ("儿子", 3), ("公子", 3), ("少爷", 3),
    ("先生", 2), ("太子", 2), ("皇帝", 2), ("王爷", 2), ("侯爷", 2), ("老爷", 2),
    ("师兄", 2), ("师弟", 2), ("哥哥", 2), ("弟弟", 2), ("汉子", 2), ("大叔", 2),
    ("男", 1),
):
    _EVIDENCE[_w] = ("male", _n)

# 一条正则一次扫完:候选按长度倒序,长词先匹配——「夫人」才不会被拆成男性的「夫」
_EVIDENCE_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(_EVIDENCE, key=len, reverse=True))
)


def infer_gender(*texts: object) -> tuple[str, str]:
    """从档案文本推断性别,返回 (gender, 证据人话)。

    分不清(证据打平或一条没有)就返回 ("", "")——由用户在卡上拍板,
    绝不硬猜:猜错的性别混在一段外貌描述里,用户很难发现是哪里出的问题。
    """
    score = {"female": 0, "male": 0}
    hits: dict[str, int] = {}
    for text in texts:
        s = str(text or "")
        if not s:
            continue
        for m in _EVIDENCE_RE.finditer(s):
            word = m.group(0)
            g, weight = _EVIDENCE[word]
            score[g] += weight
            hits[word] = hits.get(word, 0) + 1
    if score["female"] == score["male"]:
        return "", ""
    gender = "female" if score["female"] > score["male"] else "male"
    same = [(w, n) for w, n in hits.items() if _EVIDENCE[w][0] == gender]
    same.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    why = "、".join(f"「{w}」×{n}" for w, n in same[:3])
    return gender, f"档案里出现 {why}" if why else ""


# =============== 事后校验:这张卡的描述跟标定的性别打架了吗 ===============
# 只用「一眼看出性别」的外貌/声线词,不用泛化的代词——外貌段里本来就少有代词,
# 而「男装」「男式」这类不进列表,免得把「女扮男装」误报成写错性别。
_LOOK_WORDS: dict[str, tuple[str, ...]] = {
    "female": ("少女", "女子", "女性", "女人", "女孩", "女声", "姑娘", "少妇", "女童"),
    "male": ("少年", "男子", "男性", "男人", "男孩", "男声", "喉结", "胡茬", "络腮", "男童"),
}
_LOOK_EN: dict[str, tuple[str, ...]] = {
    "female": ("woman", "women", "female", "girl", "feminine", "lady"),
    "male": ("man", "men", "male", "boy", "masculine", "guy"),
}
_OPPOSITE = {"female": "male", "male": "female"}


def conflicting_words(gender: object, *texts: object) -> list[str]:
    """挑出与 gender 相反的性别词(去重,最多 4 个),用于「疑似写错性别」提示。"""
    g = normalize_gender(gender)
    if g not in _OPPOSITE:
        return []
    other = _OPPOSITE[g]
    joined = " ".join(str(t or "") for t in texts)
    if not joined.strip():
        return []
    low = joined.lower()
    out: list[str] = []
    for w in _LOOK_WORDS[other]:
        if w in joined and w not in out:
            out.append(w)
    for w in _LOOK_EN[other]:
        # 英文要词边界:\bman\b 不会命中 woman / human,\bmale\b 不会命中 female
        if re.search(rf"\b{w}\b", low) and w not in out:
            out.append(w)
    return out[:4]


def gender_conflict_note(card) -> str:
    """卡上的性别与描述打架时的一句人话提示(不打架返回空串)。

    只提示不擅自改写:改写别人写好的外貌段风险更大(可能是「女扮男装」的桥段),
    到底怎么算由用户看一眼定。
    """
    g = normalize_gender(getattr(card, "gender", ""))
    if g not in _OPPOSITE:
        return ""
    hits = conflicting_words(
        g,
        getattr(card, "appearance_cn", ""),
        getattr(card, "appearance_en", ""),
        getattr(card, "outfit_cn", ""),
        getattr(card, "voice_desc", ""),
        getattr(card, "ref_prompt_cn", ""),
        getattr(card, "ref_prompt_en", ""),
    )
    if not hits:
        return ""
    return (
        f"这张卡标的是{_LABELS[g]}性,但描述里出现了「{'、'.join(hits)}」。"
        "如果是「女扮男装」这类桥段可以不管;不是的话,直接改下面的文字,"
        "或点「按性别重出」让 AI 按正确性别重写这张卡。"
    )
