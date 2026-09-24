# app/engines/skills/packs.py
# -*- coding: utf-8 -*-
"""创作 Skill 包引擎:官方包 seed、按节点检索、注入块渲染与预算闸(docs/21)。

与 tendency 体系(倾向标签/手法卡/文风画像)的分工:tendency 是「每本书勾什么
生效」的散装约束;Skill 包是「成套、随版本分发的工艺」。三个注入纪律
(docs/21 §3.2,防「31 块全量灌」回潮):

1. 不新增模板占位符之外的模板改动——注入一律渲染成块交给既有 format 槽;
2. 注入预算硬闸:单次生成 ≤INJECT_CHAR_BUDGET 字、单节点 ≤MAX_PACKS_PER_NODE 包;
3. 按节点分发:包条目声明 node,组装时过滤——对白包永远不进分镜,反之亦然。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import SkillPack

logger = logging.getLogger("jarvis-write.skills")

INJECT_CHAR_BUDGET = 1500   # 单次生成 skill 注入总字数上限(含包名行)
MAX_PACKS_PER_NODE = 2      # 单节点同时挂载的包数上限
HISTORY_KEEP = 10           # 每包保留的编辑历史版本数

# 条目形态:directive=指令文本(进 prompt);param=参数(渲染成「键: 值」行);
# ban=排除清单;format=渲染工艺开关(不进 prompt,由引擎读 pack_key 分支)
VALID_KINDS = {"directive", "param", "ban", "format"}
VALID_NODES = {"idea", "outline", "draft", "polish", "shots", "render"}

ANIME_SHOTCARD_PACK_KEY = "anime-shotcard-render"

# ---- 首批官方包(docs/21 §4;seed 幂等,用户改过的一律不覆盖) ----
BUILTIN_PACKS: list[dict] = [
    {
        "pack_key": "storyboard-basics",
        "name": "分镜功底包",
        "description": "分镜工序的工艺下限:单镜一个主动作、时长纪律、景别交替、镜间衔接。作用于分镜生成。",
        "scope": ["anime"],
        "entries": [
            {"node": "shots", "kind": "directive",
             "directive": "每镜只安排一个主动作,把动作写到「怎么做」(肢体/视线/节奏);"
                          "相邻镜头必须有衔接逻辑(动作顺势接/视线引导/遮挡转场),不许硬跳。"},
            {"node": "shots", "kind": "param",
             "params": {"单镜时长上限": "5 秒", "每镜主动作": "1 个",
                        "景别交替": "相邻两镜景别不雷同,特写后接中景/全景缓一拍"}},
        ],
    },
    {
        "pack_key": ANIME_SHOTCARD_PACK_KEY,
        "name": "动漫镜头卡渲染工艺包",
        "description": "整集提示词换镜头卡工艺:一镜一卡(画面卡 60-120 字+运动卡零外貌词),"
                       "身份靠定妆照/末帧首帧钉死,负面词逐镜独立——治「提示词很长、出片很差」。",
        "scope": ["anime"],
        "entries": [
            {"node": "render", "kind": "format",
             "directive": "镜头卡制:逐镜产出画面卡/运动卡/音色/规避项,引擎逐镜给首帧指引"
                          "(定妆照或上一镜末帧);替换旧的分段长文模式。"},
        ],
    },
]


def ensure_builtin_packs(db: Session) -> None:
    """官方包 seed(幂等):缺哪条补哪条;已存在的(哪怕被用户改过)一律不覆盖。"""
    existing = {p.pack_key for p in db.query(SkillPack).all()}
    for spec in BUILTIN_PACKS:
        if spec["pack_key"] in existing:
            continue
        db.add(SkillPack(
            pack_key=spec["pack_key"], name=spec["name"],
            description=spec["description"], scope=list(spec["scope"]),
            entries=[dict(e) for e in spec["entries"]],
            version=1, history=[], enabled=True, is_builtin=True,
        ))
        logger.info("seed 官方 Skill 包:%s", spec["pack_key"])
    db.commit()


def normalize_entries(entries: object) -> list[dict]:
    """条目归一(API 编辑与 seed 共用一套口径):裁剪、验形态,不合格抛 ValueError。"""
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries 应该是非空数组")
    out: list[dict] = []
    for e in entries[:12]:
        if not isinstance(e, dict):
            continue
        node = str(e.get("node") or "").strip()
        kind = str(e.get("kind") or "").strip()
        if node not in VALID_NODES:
            raise ValueError(f"条目节点「{node}」不在白名单:{sorted(VALID_NODES)}")
        if kind not in VALID_KINDS:
            raise ValueError(f"条目形态「{kind}」不在白名单:{sorted(VALID_KINDS)}")
        item: dict = {"node": node, "kind": kind}
        directive = str(e.get("directive") or "").strip()
        if directive:
            item["directive"] = directive[:600]
        if isinstance(e.get("params"), dict) and e["params"]:
            item["params"] = {
                str(k).strip()[:30]: str(v).strip()[:80]
                for k, v in list(e["params"].items())[:10]
            }
        if isinstance(e.get("ban_list"), list) and e["ban_list"]:
            item["ban_list"] = [str(b).strip()[:40] for b in e["ban_list"][:20] if str(b or "").strip()]
        if kind == "directive" and not item.get("directive"):
            raise ValueError("directive 条目必须有指令文本")
        if kind == "param" and not item.get("params"):
            raise ValueError("param 条目必须有 params 键值")
        if kind == "ban" and not item.get("ban_list"):
            raise ValueError("ban 条目必须有 ban_list 清单")
        out.append(item)
    if not out:
        raise ValueError("entries 里一个可用条目都没有")
    return out


def render_pack_block(pack: SkillPack) -> str:
    """单包 → 注入文本块(format 条目是工艺开关,不是 prompt 材料,不渲染)。"""
    lines: list[str] = [f"《{pack.name}》(v{pack.version})"]
    for e in pack.entries or []:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        if kind == "directive" and (e.get("directive") or "").strip():
            lines.append(f"- {e['directive'].strip()}")
        elif kind == "param" and isinstance(e.get("params"), dict):
            for k, v in e["params"].items():
                lines.append(f"- {k}:{v}")
        elif kind == "ban":
            bans = e.get("ban_list")
            if isinstance(bans, list) and bans:
                lines.append("- 禁止出现:" + "、".join(str(b) for b in bans))
    return "\n".join(lines)


def active_packs(db: Session, *, scope: str, node: str) -> list[SkillPack]:
    """某线某节点当前生效的包:启用 + scope 命中 + 条目覆盖该节点;预算闸裁剪。

    裁剪顺序:先按单节点包数上限截断,再按整包字数粒度丢弃超预算的——
    宁可少注入一包,不把两条工艺各注一半(半截约束比没有更糟)。
    """
    ensure_builtin_packs(db)
    packs = db.query(SkillPack).filter(SkillPack.enabled.is_(True)).all()
    hits = [
        p for p in packs
        if scope in (p.scope or [])
        and any(isinstance(e, dict) and e.get("node") == node for e in (p.entries or []))
    ][:MAX_PACKS_PER_NODE]
    within: list[SkillPack] = []
    used = 0
    for p in hits:
        size = len(render_pack_block(p))
        if used + size > INJECT_CHAR_BUDGET:
            logger.warning("Skill 包《%s》超出注入预算(已用 %d/%d),本次未注入",
                           p.name, used, INJECT_CHAR_BUDGET)
            continue
        used += size
        within.append(p)
    return within


def render_skill_block(db: Session, *, scope: str, node: str) -> str:
    """生效包 → 一块可注入文本;没有包生效时返回空串(模板槽吃空串零副作用)。"""
    packs = active_packs(db, scope=scope, node=node)
    if not packs:
        return ""
    body = "\n".join(render_pack_block(p) for p in packs)
    return f"【创作 Skill(启用中的工艺包)】\n{body}"
