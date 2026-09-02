# app/engines/consistency/repetition.py
# -*- coding: utf-8 -*-
"""重复用词检测(借鉴 KazKozDev coherenceManager 的 RepetitionConstraints)。

纯 Python n-gram 统计,不调 LLM:扫描最近几章正文,找出高频重复的
词组/短语,生成"避免清单"注入写作 Prompt,防止 AI 老用同一个比喻。
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

# 检测的 n-gram 长度(中文字符),过短误报多,过长命中少
_NGRAM_SIZES = (4, 5, 6)
_MIN_REPEAT = 3          # 出现 >= 3 次算重复
_MAX_ITEMS = 12          # 避免清单最多几条
# 常见虚词开头的组合不算(误报过滤)
_STOP_HEADS = tuple("的了是在有和与就都也又还被把对从向让")


def _clean_text(text: str) -> str:
    return re.sub(r"[\s\W a-zA-Z0-9]+", "", text)


def find_repeated_phrases(texts: list[str]) -> list[tuple[str, int]]:
    """跨章统计高频 n-gram,返回 [(短语, 次数)],按次数降序。"""
    joined = _clean_text("".join(texts))
    counter: Counter[str] = Counter()
    for n in _NGRAM_SIZES:
        for i in range(len(joined) - n + 1):
            gram = joined[i : i + n]
            if gram[0] in _STOP_HEADS:
                continue
            counter[gram] += 1

    # 去掉被更长短语覆盖的子串(如"心如刀绞"覆盖"如刀绞")
    frequent = [(g, c) for g, c in counter.items() if c >= _MIN_REPEAT]
    frequent.sort(key=lambda x: (-x[1], -len(x[0])))
    kept: list[tuple[str, int]] = []
    for gram, cnt in frequent:
        if any(gram in longer for longer, _ in kept):
            continue
        kept.append((gram, cnt))
        if len(kept) >= _MAX_ITEMS:
            break
    return kept


def avoid_block(texts: list[str]) -> str:
    """渲染注入 Prompt 的"避免重复"块;无重复时返回空串。

    两级:句子级(硬,禁止整句照抄)在前,词组级(软,提醒换表达)在后。
    句子级治"签名句复读":模型一旦把某句有画面感的短句当本书意象,就会逐字
    照抄——短句不受段落去重保护(那套为护 refrain),必须在这层给模型一个
    明确的"禁止整句复用"清单来压制。
    """
    repeated_sents = find_repeated_sentences(texts)
    repeated = find_repeated_phrases(texts)
    parts: list[str] = []
    if repeated_sents:
        lines = [f"- 「{s}」(近几章出现 {c} 次)" for s, c in repeated_sents]
        parts.append(
            "【禁止逐字复用的句子】以下句子在近几章被反复写成原句(模型复读),"
            "本章一律不得整句照抄,必须换一种说法、换一个角度重新描写同一样东西:\n"
            + "\n".join(lines)
        )
    if repeated:
        lines = [f"- “{g}”(近期已用 {c} 次)" for g, c in repeated]
        parts.append(
            "【避免重复的表达】以下短语近期使用过于频繁,请换用不同表达:\n"
            + "\n".join(lines)
        )
    return "\n\n".join(parts)


# ---- 句子级跨章查重(治"同一句话反复复读") ----
# 与上面的词组级 n-gram 互补:词组级抓"比喻/套话反复",这里整句原文抓
# "签名句复读"。只做逐字(去空白/去引号后完全一致)判定,不做近似匹配,
# 宁漏勿误伤有意营造的呼应/复沓。短句(对白、语气词、< _MIN_SENT_CHARS 的
# 碎句)一律豁免。返回 [(句子, 跨章出现次数)],按次数降序。
_QUOTE_CHARS = "「」『』“”‘’\"'《》"
_MIN_SENT_REPEAT = 2   # 同一句在近几章累计出现 >= 2 次即算复读
_MIN_SENT_CHARS = 8    # 归一化后不足此长度的碎句不判(对白/语气词)
_MAX_SENTENCES = 6     # 句子级禁用清单最多几条


def _norm_sentence(s: str) -> str:
    """句子判重键:剥空白与成对引号,只留正文字符,吸收排版/转写差异。"""
    return re.sub(r"[\s%s]+" % _QUOTE_CHARS, "", s)


def _split_sentences(text: str) -> list[str]:
    """把正文切成句/分句单元(丢标点,归一化后返回)。

    按句末标点(。！？；)切句,再按逗号(，、,)切成更小的分句单元——
    否则"他掬水洗脸,水流冲过他的手指,凉凉的,有点麻。"这种一长句里反复
    出现的签名分句会被整句罩住查不出(用户看到的正是这类)。短碎句由
    _MIN_SENT_CHARS 过滤,不至于误伤对白。
    """
    cleaned = re.sub(r"[\s%s]+" % _QUOTE_CHARS, "", text)
    parts = re.split(r"[。！？!?；;，,、]+", cleaned)
    return [p for p in parts if len(p) >= _MIN_SENT_CHARS]


def find_repeated_sentences(texts: list[str]) -> list[tuple[str, int]]:
    """跨章统计逐字重复的原句,返回 [(句子, 次数)],按次数降序。"""
    counter: Counter[str] = Counter()
    for t in texts:
        for s in set(_split_sentences(t)):  # 单章内去重:只计'跨章'出现的次数
            counter[s] += 1
    hits = [(s, c) for s, c in counter.items() if c >= _MIN_SENT_REPEAT]
    hits.sort(key=lambda x: (-x[1], -len(x[0])))
    return hits[:_MAX_SENTENCES]


# ---- 章内重复段落去重(治模型"复读"bug:整段逐字重复或近似重复) ----
# find_repeated_phrases 治的是跨章高频短语(prompt 层预防);这里治的是"同一章里
# 一整段被写了两遍"——纯规则、零成本的落库前定点删除。二者互补,勿混为一谈。
_MIN_DEDUP_CHARS = 30    # 归一化后 >= 此长度的"实质段落"才判重;短句/对话/refrain/分隔符一律豁免
_NEAR_DUP_RATIO = 0.90   # 近似重复(仅差一两字)判重相似度阈值
_NEAR_WINDOW = 3         # 近似判重只回看最近 N 个实质段落(防误伤远处的有意呼应)


def _dedup_key(para: str) -> str:
    """段落判重键:剥掉所有空白(含全角空格/换行),只比正文字符,吸收无意义排版差异。"""
    return re.sub(r"\s+", "", para)


def dedup_paragraphs(text: str) -> tuple[str, int]:
    """删掉章内重复段落(模型复读),保留首次出现、删掉后续。返回 (清理后正文, 删除段数)。

    保守优先(宁漏勿误伤读者有意为之的呼应):
    - 只对归一化后 >= _MIN_DEDUP_CHARS 的"实质段落"判重;更短的行(短句/对白/场景分隔符/
      刻意 refrain)一律原样保留,也不进判重记忆。
    - 逐字重复(去空白后完全相同)按全章去重——一整章里冒出两遍的长叙述段几乎必是复读。
    - 近似重复(只差一两个字)仅在相邻 _NEAR_WINDOW 段内、相似度 >= _NEAR_DUP_RATIO 才删。
    - 段落切分与前端 splitParas/nthParaSpan 同口径(非空行=段落),保留原有换行结构不重排版,
      仅把删段后残留的连续空行压回单个空行。
    """
    lines = text.split("\n")
    seen_exact: set[str] = set()   # 全章已出现的实质段落键(逐字重复)
    recent_keys: list[str] = []    # 最近若干实质段落键(近似重复回看窗口)
    out: list[str] = []
    removed = 0
    for line in lines:
        key = _dedup_key(line)
        if len(key) < _MIN_DEDUP_CHARS:
            out.append(line)  # 短行/空行:原样保留,不参与判重
            continue
        dup = key in seen_exact
        if not dup:
            for prev in recent_keys[-_NEAR_WINDOW:]:
                if difflib.SequenceMatcher(None, key, prev).ratio() >= _NEAR_DUP_RATIO:
                    dup = True
                    break
        if dup:
            removed += 1
            continue  # 丢掉这段;残留空行在收尾统一压缩
        seen_exact.add(key)
        recent_keys.append(key)
        out.append(line)
    if not removed:
        return text, 0
    # 删段可能留下 3+ 连续换行(段落间空行叠加),压回段间单空行,首尾收拾干净
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned, removed
