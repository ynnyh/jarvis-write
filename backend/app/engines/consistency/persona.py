# app/engines/consistency/persona.py
# -*- coding: utf-8 -*-
"""核心人物画像:架构生成后从概念+角色动力学提炼结构化画像卡,落故事圣经。

为什么单列:人物的「本性」(性格底色/说话方式/底线禁忌)此前只活在架构的
角色动力学散文里,生成与门禁注入的是状态事实(受伤/持有/关系)——
人物跑偏的根因就是「本性」没有稳定的注入位。画像卡挂在 Entity.base_profile
的 persona 子字典(不新增表,老数据零迁移),供:
  ① 章节生成 personas_block 稳定注入(草稿/定稿都吃);
  ② 门禁比对台词/行事是否违背底色、是否触碰底线禁忌;
  ③ 人物卡看板结构化编辑(P2);④ 立绘提示词素材(P3)。

persona.source 记录画像来历:architecture(架构提炼)/ author(作者手改)。
再生成架构重提炼时**跳过 author 来源**——作者亲手改过的画像绝不被 AI 覆盖。

失败降级:提炼失败只 log warning,绝不阻塞架构链路(对齐 clock/canon 的隔离范式)。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Entity, Project
from app.engines.common import ask_llm_json
from app.llm.router import Task, get_adapter_for

logger = logging.getLogger("jarvis-write.persona")

# 画像最多落几张卡:开书阶段只需要主角+核心配角;配角戏份起来了章后抽取自然会立卡
MAX_CAST = 5
# 渲染进 prompt 的画像字段与上限(行预算敏感:硬约束块只有 40 行状态预算的量级)
_STR_FIELDS = ("logline", "appearance", "speech", "motive", "fear", "arc")
_LIST_FIELDS = ("traits", "never_do")
# 元字段:随画像存取(P3 立绘提示词/头像 URL)但**不渲染进生成 prompt**——
# 它们是看板/立绘素材,不是写正文的指令,塞进 prompt 白白占行预算
_META_FIELDS = ("portrait_prompt", "avatar")
_FIELD_LABEL = {
    "logline": "人设",
    "appearance": "形象",
    "traits": "底色",
    "speech": "说话",
    "motive": "动机",
    "fear": "软肋",
    "arc": "弧光",
    "never_do": "绝不做",
}
_FIELD_LIMIT = {"logline": 40, "appearance": 30, "speech": 24, "motive": 40, "fear": 24, "arc": 30}


def coerce_persona(raw: object) -> dict:
    """把 LLM 输出/前端提交归一成干净 persona dict(脏值丢弃,不抛错)。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in _STR_FIELDS:
        val = str(raw.get(key) or "").strip()
        if val:
            out[key] = val
    for key in _LIST_FIELDS:
        vals = raw.get(key)
        if isinstance(vals, list):
            cleaned = [str(v).strip() for v in vals if str(v).strip()]
            if cleaned:
                out[key] = cleaned[:4]
    for key in _META_FIELDS:
        val = str(raw.get(key) or "").strip()
        if val:
            out[key] = val
    src = str(raw.get("source") or "").strip()
    if src in ("architecture", "author", "extract"):
        out["source"] = src
    return out


def render_persona_lines(entity: Entity) -> str | None:
    """单个实体的画像行;无画像数据返回 None。字段超限硬截,行预算优先。"""
    persona = coerce_persona((entity.base_profile or {}).get("persona"))
    if not persona:
        return None
    parts: list[str] = []
    for key in ("logline", "appearance", "speech", "motive", "fear", "arc"):
        val = str(persona.get(key) or "").strip()
        if val:
            parts.append(f"{_FIELD_LABEL[key]}:{val[:_FIELD_LIMIT.get(key, 30)]}")
    for key in _LIST_FIELDS:
        vals = [str(v).strip() for v in (persona.get(key) or []) if str(v).strip()]
        if vals:
            parts.append(f"{_FIELD_LABEL[key]}:{'、'.join(vals[:4])[:40]}")
    if not parts:
        return None
    aliases = [a for a in (entity.aliases or []) if a and a != entity.name]
    name = entity.name + (f"({'、'.join(aliases[:2])})" if aliases else "")
    return f"· {name}——" + "|".join(parts)


EXTRACT_PROMPT = """\
你是资深故事策划。根据这本书的概念与角色动力学,提炼出 {max_cast} 个核心人物(主角优先)的
结构化「人物画像」。画像将贯穿全书注入每次生成,并作为一致性门禁的人物比对基准。

【故事概念】
{concept}

【角色动力学(架构产出)】
{dynamics}

要求:
1. 只收主角与开篇就会登场的核心角色,最多 {max_cast} 个;名字必须与角色动力学里的一致
2. 每个字段都要具体、可执行,写成「写正文时能直接照着做」的程度,不要空泛形容词堆砌:
   - logline 一句话人设(身份+最鲜明的特质,40 字内)
   - appearance 外形气质(年龄感/穿着/气场,30 字内)
   - traits 性格底色 2-4 个词(如:隐忍腹黑、热烈直球)
   - speech 说话方式/口癖(句式长短、口头禅、语气,24 字内)
   - motive 表层目标与深层动机(40 字内)
   - fear 恐惧/软肋(24 字内)
   - arc 人物弧光:从哪里成长/沉沦到哪里(30 字内)
   - never_do 底线禁忌 1-3 条:这个角色绝不会做的事(如「绝不亲手杀人」),门禁会据此查崩人设
3. 未提及的字段宁可留空也不要编造与既有设定矛盾的设定

严格按 JSON 输出(不要 markdown 围栏,不要任何解释):
{{
  "characters": [
    {{
      "name": "角色名", "logline": "…", "appearance": "…",
      "traits": ["…"], "speech": "…", "motive": "…", "fear": "…",
      "arc": "…", "never_do": ["…"]
    }}
  ]
}}"""


def _concept_text(concept: object) -> str:
    if isinstance(concept, dict):
        parts = [
            str(concept.get(k) or "").strip()
            for k in ("logline", "protagonist", "conflict", "hook", "setting", "signature")
        ]
        return "\n".join(p for p in parts if p) or "(无结构化概念)"
    return str(concept or "").strip() or "(无)"


async def extract_cast_profiles(db: Session, project: Project) -> int:
    """架构生成后提炼核心人物画像卡并 upsert 进圣经。返回落库张数。

    幂等与覆盖语义:同名实体已有 author 来源画像 → 跳过(作者手改优先);
    其余覆盖/补全;同名实体不存在则新建。失败抛给调用方降级(不 commit)。
    """
    arch = project.architecture
    dynamics = (arch.character_dynamics if arch else "") or ""
    if not dynamics.strip() and not str(project.concept or "").strip():
        return 0

    prompt = EXTRACT_PROMPT.format(
        max_cast=MAX_CAST,
        concept=_concept_text(project.concept),
        dynamics=dynamics.strip()[:6000],
    )
    data, parse_err = await ask_llm_json(
        get_adapter_for(Task.ARCHITECTURE), prompt, label="提炼核心人物画像"
    )
    if parse_err:
        raise ValueError(f"人物画像解析失败:{parse_err}")
    rows = [r for r in (data.get("characters") or []) if isinstance(r, dict)]
    if not rows:
        return 0

    from app.engines.consistency.bible import BibleService

    bible = BibleService(db, project.id)
    saved = 0
    for row in rows[:MAX_CAST]:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        persona = coerce_persona({**row, "source": "architecture"})
        if not persona:
            continue
        ent = bible.find_entity(name)
        if ent is None:
            ent = Entity(
                project_id=project.id,
                entity_type="character",
                name=name,
                aliases=[],
                base_profile={},
            )
            db.add(ent)
            db.flush()
        existing = coerce_persona((ent.base_profile or {}).get("persona"))
        if existing.get("source") == "author":
            continue  # 作者手改过的画像绝不被 AI 覆盖
        profile = dict(ent.base_profile or {})
        profile.setdefault("profile", str(persona.get("logline") or "").strip())
        profile["persona"] = persona
        ent.base_profile = profile
        saved += 1
    logger.info("第《%s》人物画像落库 %d 张", project.title, saved)
    return saved


# ---------- P3 立绘提示词:从画像拼角色形象图 prompt(项目边界:只产提示词,不接绘图模型) ----------
PORTRAIT_PROMPT = """\
你是资深 AI 绘画提示词工程师。根据下面的人物画像,为这个角色写立绘/人物形象的绘图提示词,
中英各一版(英文给 MJ/SD 类模型,中文给即梦等国产模型),用于生成单人身立绘。

【角色名】{name}
【人物画像】
{persona}

要求:
1. 只描述「这个人长什么样、穿什么、什么气质、什么姿势构图」,不要发明画像里没有的剧情设定
2. 英文版用逗号分隔的关键词风格(1girl/1boy 不用,用 portrait of…),含画风建议词;
   中文版是一段通顺的描述;两版信息一致
3. 各 80-150 字/词,纯外观,不写背景故事

严格按 JSON 输出(不要 markdown 围栏):
{{"prompt_cn": "中文提示词", "prompt_en": "english prompt"}}"""


async def build_portrait_prompt(name: str, persona: dict) -> dict:
    """从画像生成中英双语立绘提示词。返回 {prompt_cn, prompt_en};LLM 失败向上抛。"""
    persona_lines = [
        f"- {_FIELD_LABEL[k]}:{persona[k]}"
        for k in ("logline", "appearance", "traits", "speech", "motive", "arc")
        if str(persona.get(k) or "").strip()
    ]
    prompt = PORTRAIT_PROMPT.format(
        name=name, persona="\n".join(persona_lines) or "(画像为空,按角色名自由发挥)"
    )
    data, parse_err = await ask_llm_json(
        get_adapter_for(Task.SUMMARY), prompt, label=f"{name} 立绘提示词"
    )
    if parse_err:
        raise ValueError(f"立绘提示词解析失败:{parse_err}")
    return {
        "prompt_cn": str(data.get("prompt_cn") or "").strip(),
        "prompt_en": str(data.get("prompt_en") or "").strip(),
    }
