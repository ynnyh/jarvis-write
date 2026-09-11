# app/engines/media/markdown.py
# -*- coding: utf-8 -*-
"""三条出片线共用的 Markdown 装配件:标题 / 表格 / 摘要行 / 台词清单 / 三轨提示词块。

**为什么要单列一份**:四条线(宣传片、漫剧、情绪短片、生日祝福)的导出手册
各写一遍「拼行 → 补空行 → join」,同一个表格转义抄了七遍,而且抄歪了——
宣传片与漫剧把单元格里的换行压平了(`.replace("\\n", " ")`),情绪短片与
生日祝福没有:台词里带一个换行,整张表就被拆掉。同一件事在四个文件里有两种
做法,是这批债里最典型的形态。收成一份之后,「表头 / 分隔行 / 数据行」的拼法
与「缺值怎么显示」的口径都只有一处。

放这里的东西必须满足两条(与 `media/__init__.py` 同一纪律):
①**不含任何一条线的业务口径** —— 章节标题、列名、文案标签一律由调用方传入,
本模块只管**怎么拼**;②纯确定性,不调 LLM。

`blank` 参数的默认值按**多数用法**取:标题之后留空行(h3 例外,它下面通常
直接跟正文块),清单/表格之后由调用方决定。这不是装饰——导出文本是用户拿去
干活的,行距变了就是「同一份手册两样」。
"""
from __future__ import annotations

# 三轨提示词块的默认标签(情绪短片与生日祝福用这套;宣传片/漫剧传自己的长标签)。
# 标签是**文案**不是口径,所以放在常量里由调用方显式引用,而不是写死在函数体里。
CN_TRACK_LABEL = "**中文(即梦/可灵)**"
EN_TRACK_LABEL = "**英文(MJ)**"
NEG_TRACK_LABEL = "**负面**"

# 生成切段的默认小节标题(情绪短片与生日祝福口径一致)
SEGMENTS_TITLE = "生成切段(一段一次生成,画布拼接)"


def cell(v: object) -> str:
    """表格单元格:竖线转义成 `/`(留着会拆列)、换行压平(留着会断行)。

    `\\r\\n` / `\\r` 一并收敛成空格:Windows 上贴过来的文本带 `\\r`,只处理 `\\n`
    的话 `\\r` 会留在行里,Excel 与部分编辑器会把它当断行。
    裸 `None` 出空串(表格里写 "None" 比空着难看得多);`0` 出 "0"。
    """
    if v is None:
        return ""
    return (
        str(v).replace("\r\n", "\n").replace("\r", "\n")
        .replace("|", "/").replace("\n", " ")
    )


def table_rows(header: list[str], rows: list[list]) -> list[str]:
    """表头 + 分隔行 + 数据行(GFM 表格),**不带**尾随空行。

    尾随空行交给调用方:有的地方表格后面紧跟条件分支(如宣传片的 Slogan 候选),
    那里自己会补空行,再补一次就多一个空行。
    """
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out.extend("| " + " | ".join(cell(c) for c in row) + " |" for row in rows)
    return out


class Md:
    """Markdown 行装配器:一路 `append`,最后 `text()` 一次性 join。

    替代「`L: list[str]` + 满屏 `L.append("")`」的写法:空行的位置由小节方法
    负责,调用方只写内容。`lines` 直接暴露,方便和既有返回 `list[str]` 的函数互操作。
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    # ---------- 逃生口 ----------
    def add(self, *lines: str) -> "Md":
        """原样追加(含空行):留给没有对应小节方法的零碎行。"""
        self.lines.extend(lines)
        return self

    def extend(self, lines: list[str]) -> "Md":
        self.lines.extend(lines)
        return self

    # ---------- 结构 ----------
    def h1(self, text: str, *, blank: bool = True) -> "Md":
        return self._head(1, text, blank)

    def h2(self, text: str, *, blank: bool = True) -> "Md":
        return self._head(2, text, blank)

    def h3(self, text: str, *, blank: bool = False) -> "Md":
        """默认不留空行:三级标题下面通常直接跟正文块(模板/词/表)。"""
        return self._head(3, text, blank)

    def _head(self, level: int, text: str, blank: bool) -> "Md":
        self.lines.append(f"{'#' * level} {text}")
        if blank:
            self.lines.append("")
        return self

    # ---------- 内容 ----------
    def bullet(self, text: str, *, blank: bool = False) -> "Md":
        self.lines.append(f"- {text}")
        if blank:
            self.lines.append("")
        return self

    def kv(self, pairs: list[tuple[str, str]], *, blank: bool = True) -> "Md":
        """单行摘要 `- k:v | k:v`:手册开头那几行元信息(主题/时长/画风…)。"""
        self.lines.append("- " + " | ".join(f"{k}:{v}" for k, v in pairs))
        if blank:
            self.lines.append("")
        return self

    def quote(self, text: str, *, blank: bool = False) -> "Md":
        self.lines.append(f"> {text}")
        if blank:
            self.lines.append("")
        return self

    def speech(self, items: list, *, numbered: bool = False, blank: bool = True) -> "Md":
        """台词/文案清单:`- **说话人**:文本`(或 `1. **说话人**:文本`)。

        非 dict 的条目跳过(LLM 回流数据里混进字符串是常态),**一律加粗说话人**:
        手册是拿来照着念的,念的人要先看见「这句谁说的」。
        """
        for i, line in enumerate(items, start=1):
            if not isinstance(line, dict):
                continue
            body = f"**{line.get('speaker', '')}**:{line.get('text', '')}"
            self.lines.append(f"{i}. {body}" if numbered else f"- {body}")
        if blank:
            self.lines.append("")
        return self

    def table(self, header: list[str], rows: list[list], *, blank: bool = True) -> "Md":
        self.lines.extend(table_rows(header, rows))
        if blank:
            self.lines.append("")
        return self

    # ---------- 三条线共用的两个整块 ----------
    def style_anchor(self, style_cn: str, style_en: str, negative: str) -> "Md":
        """画风锚小节:中文锚 / 英文锚 / 负面词基座(三行,顺序固定)。"""
        self.h2("画风锚")
        self.lines.append(f"- {style_cn}")
        self.lines.append(f"- EN: {style_en}")
        self.lines.append(f"- 负面基座:{negative}")
        self.lines.append("")
        return self

    def shot_tracks(
        self,
        seq: object,
        shot_type: str,
        camera: str,
        duration_s: object,
        prompt_cn: str = "",
        prompt_en: str = "",
        negative: str = "",
        *,
        cn_label: str = CN_TRACK_LABEL,
        en_label: str = EN_TRACK_LABEL,
        neg_label: str = NEG_TRACK_LABEL,
        blank: bool = True,
    ) -> "Md":
        """一格分镜的三轨提示词块(### 镜头 … + 中文 / 英文 / 负面)。

        缺轨道时写「(未生成)」而不是留空:留空分不清「没生成」和「生成失败」,
        用户照着复制会粘个空框过去。
        """
        self.h3(f"镜头 {seq}({shot_type}/{camera}/{duration_s}s)")
        self.add(cn_label, "", prompt_cn or "(未生成)", "")
        self.add(en_label, "", prompt_en or "(未生成)", "")
        self.add(f"{neg_label}:{negative or '(无)'}")
        if blank:
            self.add("")
        return self

    def segments(
        self, chunks: list, *, title: str = SEGMENTS_TITLE, blank: bool = True
    ) -> "Md":
        """生成切段清单:一段一次生成、画布拼接(段号与 SRT 同根轴)。"""
        self.h2(title)
        for c in chunks:
            seqs = "、".join(str(q) for q in c.get("shot_seqs") or [])
            over = " ⚠超限" if c.get("over_limit") else ""
            self.lines.append(
                f"- **段 {c.get('index')}**({c.get('start_s')}-{c.get('end_s')}s,"
                f"镜头 {seqs}){over}"
            )
        if blank:
            self.lines.append("")
        return self

    # ---------- 出栈 ----------
    def text(self) -> str:
        return "\n".join(self.lines)
