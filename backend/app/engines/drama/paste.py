# app/engines/drama/paste.py
# -*- coding: utf-8 -*-
"""按目标生图站拼「即拿即用」的粘贴版提示词(纯字符串,不碰 LLM/DB)。

为什么要有这一层:我们出的是三轨提示词(中文正文 / 英文 / 负面词),但生图站
长相各不相同——
- GPT-image、DALL·E、豆包、通义这类站**只有一个描述框**,没有负面框:负面词
  必须以否定句揉进正文才生效,否则用户复制过去等于把负面词丢了;
- 即梦 / 可灵 / SD·ComfyUI 有独立负面框:正反分开粘才对;
- Midjourney 走英文 + `--ar` / `--no` 参数。
同一格分镜因此要按平台给不同的粘贴版,用户点一下就贴,不必自己拼。

负面词基座是中文(见 prompts/drama.py 的风格卡契约),中文站直接用;MJ 那版
用固定的通用英文排除表,不翻译中文负面词——翻译要么多一次 LLM 调用、要么
逐字硬翻容易漂,而这类排除项本就是行业固定套词。
"""
from __future__ import annotations

# 平台清单:key → 展示名(前端下拉按这个顺序渲染,默认第一项)
PLATFORMS: tuple[tuple[str, str], ...] = (
    ("oneframe", "只有一个描述框(GPT-image / DALL·E / 豆包 / 通义…)"),
    ("dualbox", "有负面词框(即梦 / 可灵 / SD / ComfyUI)"),
    ("mj", "Midjourney / Niji(英文 + 参数)"),
)
DEFAULT_PLATFORM = "oneframe"

# MJ 的 --no 排除表:行业通用套词,与中文负面词基座语义对应
_NEG_EN = (
    "text, watermark, signature, logo, extra fingers, extra limbs, "
    "deformed hands, malformed face, distorted eyes, bad anatomy, "
    "lowres, blurry, jpeg artifacts, oversaturated"
)

# 构图提示:分镜是竖屏成片用,定妆照是给后续每格当参考图用(正面半身+干净背景)
_COMPOSE = {
    "shot": "竖屏 {ratio} 构图,画面里不要出现任何文字或字幕。",
    "ref_sheet": "{ratio} 构图,单人正面半身居中、纯色干净背景、光线均匀,"
                 "这张图后续要当角色参考图用,不要加文字、边框、多人同框。",
}
_REF_RATIO = "3:4"  # 定妆照默认比例(半身正面比 9:16 更省画面)


def _join(*blocks: str) -> str:
    return "\n\n".join(b.strip() for b in blocks if b and b.strip())


def _ref_line(ref_names: tuple[str, ...] | list[str]) -> str:
    """参考图指令行:本格有定妆照的角色才出现,提醒用户上传并要求照图不改。"""
    names = [str(n).strip() for n in (ref_names or []) if str(n or "").strip()]
    if not names:
        return ""
    who = "、".join(f"「{n}」" for n in names)
    return (
        f"【参考图】请上传{who}的定妆照作为参考图:人物长相、发型、服饰"
        "严格照参考图,不得改动;只按本段描述改变动作、表情、机位与环境。"
    )


def _ensure_ar(prompt_en: str, ratio: str) -> str:
    """英文轨补 --ar(SHOT_PROMPT 契约要求模型自带,漏了这里兜底)。"""
    s = (prompt_en or "").strip()
    if not s or "--ar" in s:
        return s
    return f"{s} --ar {ratio}"


def paste_variants(
    *,
    prompt_cn: str = "",
    prompt_en: str = "",
    negative: str = "",
    ratio: str = "9:16",
    ref_names: tuple[str, ...] | list[str] = (),
    kind: str = "shot",
) -> dict[str, dict[str, str]]:
    """按平台生成粘贴版。

    返回 {platform: {label, main, negative, hint}}:
      main     直接粘进站点的主描述框(单框站已含负面否定句与构图要求)
      negative 有负面框的站粘这里;单框站为空串(已并入 main)
      hint     一句人话操作提示(比例怎么选、参考图怎么用)
    kind: "shot"(分镜格)/ "ref_sheet"(角色定妆照),只影响构图提示与默认比例。
    """
    cn = (prompt_cn or "").strip()
    neg = (negative or "").strip()
    if kind == "ref_sheet":
        ratio = ratio or _REF_RATIO
    compose = _COMPOSE.get(kind, _COMPOSE["shot"]).format(ratio=ratio)
    ref_line = _ref_line(ref_names)
    ref_hint = "在站点点「上传参考图」把定妆照传上去,再粘这段。" if ref_line else ""

    oneframe = _join(
        cn,
        ref_line,
        f"【构图】{compose}",
        f"【不要出现】{neg}" if neg else "",
    )
    dualbox = _join(cn, ref_line)
    return {
        "oneframe": {
            "label": dict(PLATFORMS)["oneframe"],
            "main": oneframe,
            "negative": "",
            "hint": "整段粘进唯一的描述框即可,负面词已改写成「不要出现」并入正文。"
                    + ref_hint,
        },
        "dualbox": {
            "label": dict(PLATFORMS)["dualbox"],
            "main": dualbox,
            "negative": neg,
            "hint": f"正文粘主框、负面词粘负面框;画面比例选 {ratio}。" + ref_hint,
        },
        "mj": {
            "label": dict(PLATFORMS)["mj"],
            "main": (
                f"{_ensure_ar(prompt_en, ratio)} --no {_NEG_EN}"
                if (prompt_en or "").strip() else ""
            ),
            "negative": "",
            "hint": "参数已带 --ar/--no;想进一步锁脸可再加 --cref <定妆照图片链接>。",
        },
    }


def shot_paste(shot, style, ref_names: tuple[str, ...] | list[str] = ()) -> dict[str, dict[str, str]]:
    """分镜格的粘贴版(便利封装:从 DramaShot + 风格卡取料)。"""
    return paste_variants(
        prompt_cn=shot.prompt_cn or "",
        prompt_en=shot.prompt_en or "",
        negative=shot.negative or "",
        ratio=(getattr(style, "ratio", "") or "9:16") if style is not None else "9:16",
        ref_names=ref_names,
        kind="shot",
    )


def ref_sheet_paste(card, style) -> dict[str, dict[str, str]]:
    """角色定妆照的粘贴版(拿去出参考图那一步用)。"""
    return paste_variants(
        prompt_cn=getattr(card, "ref_prompt_cn", "") or "",
        prompt_en=getattr(card, "ref_prompt_en", "") or "",
        negative=(getattr(style, "negative", "") or "") if style is not None else "",
        ratio=_REF_RATIO,
        kind="ref_sheet",
    )
