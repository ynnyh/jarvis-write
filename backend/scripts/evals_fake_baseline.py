# backend/scripts/evals_fake_baseline.py
# -*- coding: utf-8 -*-
"""评测底座 fake baseline:不调用 LLM,用预置假数据造一份完整 run JSON + Markdown。

目的:
- 验证 fixture → seed → chapter_record → aggregate → report → compare 的全链路
  拼接(除 generate_chapter 真实管线外,都被走一遍)
- 把黄金样本「破封纪」的第一份 baseline 范例落进 evals_out/,作为后续
  prompt 改动的对照基准

用法(在 backend/ 下):
  .venv/Scripts/python -m scripts.evals_fake_baseline

输出到 backend/evals_out/:
  baseline-po_feng_ji-<时间戳>.json          (run dict,后续 compare 用)
  baseline-po_feng_ji-<时间戳>.chapters.md   (10 章假正文,给人读)
退出码 0 = 成功。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# ----- 必须在任何 app.* 导入前把 DATABASE_URL 指到独立临时库,绝不碰 jarvis_write.db
_TMP = Path(tempfile.mkdtemp(prefix="evals-fake-baseline-"))
_DB = _TMP / "baseline.db"
import os
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("INVITE_CODE", "fake-baseline")

from app.db.base import Base  # noqa: E402
import app.db.models  # noqa: F401,E402  让所有模型都注册到 Base.metadata
from app.db.models import Chapter, Entity, Fact, LlmUsage, Project  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.evals.fixtures import load_fixture  # noqa: E402
from app.evals.runner import (  # noqa: E402
    aggregate,
    chapter_record,
    git_commit,
    manifest_fingerprint,
    prompt_manifest,
)
from app.evals import report as evals_report  # noqa: E402

EVALS_OUT = Path(__file__).resolve().parents[1] / "evals_out"


# ----- 10 章假正文:每章 ~2500 字,有意设一两条「章内复读」、一两条 AI 味套话,
#       跑 metrics 后能让 baseline 报告里 chapters 部分有数据可读。
_FAKE_BODY_TEMPLATES = [
    # 1
    "碎月初五的天空灰得像铅。陆辰站在外门考核殿外,听见自己心跳得慢。"
    "左耳深处的空鸣他早就习惯了——十岁那年冬天在北岭摔下山坡,左耳撞在岩石上,"
    "血流干了也没知觉。师尊扶他回山时只说了一句话:『左耳听不见,右耳听人生。』\n"
    "考核殿里赵师兄点名:『陆辰,修为停滞不前,五年了还在筑基初期,"
    "你还要赖在外门多久?』殿里有人笑。陆辰低头,只看自己手背。"
    "执事王师叔落锤:『陆辰,断魂崖下取寒玉一块,三日内回。』\n"
    "他出殿,雪正飘。玉佩挂在颈,贴肉微暖。",
    # 2
    "断魂崖下的风比山上更烈。陆辰用绳索降了三丈,脚踩在冰面上。"
    "雪粒打在他肩头,他不闪——左耳既然听不见,闪也无意。他学会看嘴唇来听风。\n"
    "雪狼从雾里冲出,喉结动了两下,陆辰侧身,雪狼擦肩。"
    "一颗灵气微光珠从雪底翻出,微光里是寒字『七』的影子。陆辰一把抱住。"
    "珠子被抱住的瞬间,玉佩在颈跳了一下,微温,他没在意。\n"
    "第七日晨他登崖。一身破洞,玉佩仍安静。",
    # 3
    "外门执事殿里,苏幼白例验寒玉。她俯身,把寒玉放上秤,头也不抬地问:"
    "『崖底何处采得?』陆辰回:『正中偏东。』苏幼白抬眼,目光在他破衣上停了一息。\n"
    "陆辰没多说,接玉转身。回屋,他盘腿坐下,气海深处隐隐有微温——"
    "丹田下裂纹处像被风吹过,轻,却不止。他调息了半刻钟,睁开眼。窗外雪已停。",
    # 4
    "夜修是赌命。陆辰依稀调息,丹田下裂纹随灵气拨动而扩。灵光从气海漏出,掀翻烛台。"
    "他吐了一口血。\n"
    "苏幼白破门而入,一掌按住他气海,低声诵诀。裂纹被迫回,温意退。"
    "一缕灵线从她指尖传入他右耳,他第一次清晰地听进一句师尊口吻的话:『封。』\n"
    "右耳听见的字比左耳响十倍。师姐起身时,玉佩在他颈响了一声——他没来得及看。",
    # 5
    "内门告示墙贴朱砂告示三张:『封泉会·五十载一次·外门持牌者可观礼』。"
    "陆辰默读最后一行,左耳空鸣,默数『入试者须持牌』。\n"
    "他折身要走,身后有人叫住。苏幼白递来一枚旧木牌,牌面磨得光滑。她不多话,"
    "只说一句:『北字号递到新人手里。』陆辰接过,木牌背刻字『寒字七』,字迹歪斜,"
    "像苏幼白六年前的笔。",
    # 6
    "选拔前夜气温骤降。窗外霜白,玉佩在颈浮出『寒字七十二』细水纹,"
    "水纹顺颈流入锁骨。陆辰在床上坐起。\n"
    "苏幼白破门而入。她扑跪于地,以丹田气息触佩,念出师尊尊号。佩纹立稳。"
    "师姐抬头看他:『玉是他刻的。』陆辰不敢问『他是谁』。",
    # 7
    "深夜藏经阁。陆辰抽《青云三十年封脉档》,黄纸,字旧。"
    "第七页:『封脉试验失败——道基断送——下山。』师尊名讳出现又隐去,只剩一个『承』字。\n"
    "他合卷,玉佩在颈响了一声。楼梯上他回望一眼,觉得黄纸背面有字,"
    "但阁卫催闭馆,他没看。",
    # 8
    "封泉台双灯。陆辰登台无牌,只凭苏幼白递来的木牌。台下嘘声起。\n"
    "赵师兄登台,灵压强势。陆辰从怀中抽出微光珠,丹田裂纹在承受的瞬间剧烈反噬。"
    "他吐血,但目光不散。微光珠爆闪,赵师兄后背撞上木桩,倒地。\n"
    "台下寂然无声。陆辰落台,玉佩在他颈浮出第二条水纹——裂纹已至上限。",
    # 9
    "留客殿内,苏幼白跪下。她以丹田气息浮出玉佩原纹:『寒字』二字与一侧『七』、"
    "一侧『二』,字迹清秀,与她平日的粗字不是一人笔。\n"
    "陆辰扶人,不知何言。她以姐姐辈口吻说:『封未破,人不死。』\n"
    "她抬头看他,这才第一次没把目光闪到别处。",
    # 10
    "玉简传书,内门执事落印:『许陆辰试炼位。』姐姐辈线索指向北岭深处旧宗废墟。"
    "苏幼白送来一枚旧宗地形残图,图右下角烙字『寒字七十三位』。\n"
    "陆辰接令,卷末雪山远景。玉佩水纹在他颈浮出第三纹——但他伸手按住,不让它再动。",
]


# ----- padding 用模板句生成:每章用本章的 scene_location / key_items /
#       characters_involved 作填充值,句内数字按 (章号, 句序) 推导。
#       铁律:每个 ≥8 字的分句必须含一个数字槽位({a}/{b}/{day})——
#       place/npc/obj 不算数,它们在固定 (句序,轮次) 下跨章取值相同;
#       而数字 a=3+(7n+3i+5r)%11 对固定 (i,r) 随章号遍历全部余数,
#       保证任何分句都不会跨章逐字重复、虚增评测指标。
_SENT_TEMPLATES = [
    "他在{place}站了约{b}息,数檐下的冰凌,一共{a}根,比昨日少了两根,少的两根他记到第{b}日。",
    "廊下{npc}走过,脚步在{place}的石板上响了{a}步,随后是开门声,然后没有了。",
    "{obj}在他袖中待了第{b}日,凉意顺袖口往上走了{a}寸,他没有去动它,只把袖口拢了拢。",
    "候补的第{day}日,雪比前一日厚了{a}分,他把领口按紧,沿着{place}边沿走了{b}个来回。",
    "他把{obj}取出来看了第{b}遍,又原样放回袖袋第{a}层,靠内那一侧,外面再压一层布。",
    "{npc}没有多话,只把{obj}往他那边推了{a}分,转身时衣摆扫了门槛{b}下,带起一线雪。",
    "{place}的石阶第{a}级有一道旧裂,他踩过去,雪从第{b}级边沿塌下一角,露出底下的青石。",
    "他听见{place}方向有人搬了{a}趟柴,柴捆落地,闷响一阵,停了一息,又是两下,声音到第{b}下就散了。",
    "暮色下来得慢,他看着{place}的灯笼从第{a}盏亮到第{b}盏,中间隔了约半刻,他把这半刻用在第{b}遍调息上。",
    "今日要做的事他排了第{b}遍:先去{place},再问{npc},最后把{obj}的去向核对{b}遍,顺序没有变过。",
    "风从北面来,他把身子侧了侧,让右耳迎风,左耳听不见这件事他提醒过自己{a}次,早就成习惯了。",
    "{npc}在{place}等了约{b}刻,等的人没有来,他路过时看见了,没有打招呼,继续往山下走了{a}步才回头。",
    "他从山上回来,走了{a}段路,袖袋里的{obj}沉了约{b}分,他想不明白,也没有立刻去想,先记下。",
    "灶上的水到第{b}回就凉了,他续了一次水,数着自己喝了{a}碗,第{b}碗之后就没有再续,壶底还剩一点。",
    "扫雪人在{place}扫出{a}步窄道,他沿着窄道走,鞋底只落在扫过的{a}步之内,他没量过第二遍。",
    "他把{obj}在灯下翻了第{b}遍,细缝还在背面,宽了约{a}分,他用指甲量了量,把数记在了心里。",
    "夜里他醒了一次,听见{place}有人来回走了{a}趟,走动的人在窗外停了约{b}息,又走了,他数到第{a}声才翻身。",
    "他在{place}的墙根下蹲了约{b}息,墙根的雪化得快,滴水声断断续续,他数到第{a}滴起身走了。",
    "{npc}的回话只有{a}个字,比预想的短,他把这{a}个字在心里过了两遍,没有再问第二句。",
    "他把{obj}与告示上的字对了第{b}遍,笔画对得上,落款对不上,他把这个差别记在了心里第{a}位。",
    "他把{obj}用布裹了{a}层,搁在枕边,半夜摸了一次,又摸了一次,第{b}次才睡着。",
    "他把{obj}收进内袋贴身放了{a}日,出门前拍两下,确认还在,才出门,这一趟去{place}来回用了{b}刻。",
    "他路过{place}门口{a}次,只停了一次,在心里把{obj}的位置默记了{b}遍,才继续往前。",
    "第{day}日早上他去{place}试雪,印深{a}分,到石阶口一共走了{b}步,比预想的多。",
    "他把没用完的炭收了{a}块,码成第{b}层,搁在墙角,他数过,这样能撑过{b}场雪。",
]


def _long_filler_for_chapter(n: int, outline: dict) -> str:
    """按本章 outline 生成 ~2100~2300 字的独特 padding。

    - 场景/人物/道具取自本章 outline,章节之间天然不同
    - 25 模板 × 每章 2 轮 = 50 句(~2100 字),不加丢弃规则
    - seq = (n-1)*2 + r 在全体 (章,轮) 上全局唯一 0..19:
        a = 3 + seq                (3..22,固定 (i,r) 下跨章必不同)
        b = 4 + (seq*7) % 22 + i%5 (同模板 i 固定,7Δseq ≢ 0 (mod 22),
          Δseq≤19 → b 跨章必不同) → 任何分句都不跨章逐字重复
    """
    base_place = (outline.get("scene_location") or "青云宗·外门").split("·")[-1]
    places = [base_place, f"{base_place}旁的石阶", f"{base_place}外的廊下", "外门演武场边缘"]
    npcs = outline.get("characters_involved") or ["执事"]
    objs = outline.get("key_items") or ["旧木牌"]
    day = n

    parts: list[str] = []
    for r in range(2):
        seq = (n - 1) * 2 + r
        a = 3 + seq
        b = 4 + ((seq * 7) % 22)
        for i, tpl in enumerate(_SENT_TEMPLATES):
            sent = tpl.format(
                place=places[(i + r) % len(places)],
                npc=npcs[(i * 2 + r) % len(npcs)],
                obj=objs[(i + r) % len(objs)],
                day=day,
                a=a,
                b=b + (i % 5),
            )
            parts.append(sent)
    return "".join(parts)


def _make_fake_6_tuple(n: int, final_content: str, status: str):
    """造一个 chapter_record 接受的 6 元组,每个分量都是 fake。"""
    review = SimpleNamespace(scores=None, passed=None)  # 真正的 review 是 dict
    review = {
        "scores": {
            "plot": 7 + (n % 3 - 1),  # 6~8
            "prose": 7,
            "pacing": 6 + (n % 2),
            "character": 7,
            "continuity": 7 + (n % 2),
        },
        "passed": (n != 4) and (n != 7),  # 第 4 章门禁隔离、第 7 章不过(模拟)
        "revision_rounds": 1 if n < 9 else 0,
        "repair_rounds": 0,
        "comment": f"第 {n} 章主审短评:节奏稳,描写克制;",
    }
    gate_issues = []
    if n == 4:
        gate_issues = [
            {"severity": "blocker", "message": "陆辰修为被判定突然跃升,与旧封印设定冲突"},
            {"severity": "major", "message": "玉佩水纹触发时段与季节描述不一致"},
            {"severity": "minor", "message": "若干形容词略冗余"},
            {"severity": "minor", "message": "一处'如同'虚词"},
        ]
    elif n == 7:
        gate_issues = [{"severity": "major", "message": "藏经阁时间线与前章细节不一致"}]
    else:
        # 偶数章有几个 minor
        if n % 2 == 0:
            gate_issues = [{"severity": "minor", "message": "轻声细语'}"}]
    extraction = {
        "entities": {"count": 4 + (n % 4), "new": 1 + (n % 2)},
        "facts": {"count": 6 + (n % 5), "new": 2},
    }
    guard = SimpleNamespace(action="none" if n != 4 else "rewrite")
    preflight = [] if n not in (1, 8) else [{"warning": "本章冲突可能源于大纲修订"}]
    chapter = SimpleNamespace(final_content=final_content, status=status)
    return chapter, gate_issues, extraction, guard, review, preflight


def build_fake_baseline() -> Path:
    fx = load_fixture("po_feng_ji")
    Base.metadata.create_all(engine)
    pid: int
    usage_before = 0
    with SessionLocal() as db:
        project = Project(
            title="[评测] 破封纪",
            topic=fx.topic,
            genre=fx.genre,
            target_chapters=fx.chapter_count,
            target_words_per_chapter=fx.target_words,
            global_tendency=dict(fx.global_tendency or {}),
            concept=fx.concept,
            dna=fx.dna,
            world_rules=fx.world_rules,
            review_pass_threshold=fx.review_threshold,
            status="writing",
        )
        db.add(project)
        db.flush()
        pid = project.id
        from app.engines.pipeline.architecture import ArchitectureResult, save_architecture
        from app.engines.pipeline.blueprint import save_blueprint
        save_architecture(db, project, ArchitectureResult(
            core_seed=fx.architecture["core_seed"],
            character_dynamics=fx.architecture["character_dynamics"],
            world_building=fx.architecture["world_building"],
            plot_architecture=fx.architecture["plot_architecture"],
        ))
        save_blueprint(db, project, [dict(o) for o in fx.outlines])
        db.commit()

    started = datetime.now(timezone.utc)
    t_run = time.time()
    records = []
    chapters_text = {}
    for n in range(1, fx.chapter_count + 1):
        outline = fx.outlines[n - 1]
        title = outline["title"]
        text = _FAKE_BODY_TEMPLATES[n - 1] + _long_filler_for_chapter(n, outline)
        # 第 1 / 第 7 章额外混入复读演示(故意让 baseline 出现可识别复读,
        # 让 prompt 工程师一眼看到「即便 baseline 也有复读」,改 prompt 时知道
        # 应该把指标往下压)
        if n in (1, 7):
            text = text + "他想起师姐递给他木牌时的表情。\n" * 5
        chapters_text[n] = text
        # 写 chapter 行(final_content 就是 fake 正文)
        status = "quarantined" if n == 4 else "finalized"
        with SessionLocal() as db:
            ch = Chapter(
                project_id=pid,
                chapter_number=n,
                final_content=text,
                word_count=len(text),
                outline_version_used=1,
                status=status,
            )
            db.add(ch)
            db.commit()
        # 写 LlmUsage(让 usage 数据非空)
        with SessionLocal() as db:
            for tier in ("quality", "fast", "review"):
                db.add(LlmUsage(
                    model=f"mock-{tier}",
                    prompt_tokens=1100 + n * 25,
                    completion_tokens=820 + n * 20,
                ))
            db.commit()

        rec = chapter_record(
            n, title,
            _make_fake_6_tuple(n, text, status),
            fx.target_words, seconds=10.0 + n * 0.2,
        )
        records.append(rec)

    # 抽实体 / 事实(让 bible 字段非 0)
    with SessionLocal() as db:
        entities_to_add = [
            Entity(project_id=pid, entity_type="character", name="陆辰"),
            Entity(project_id=pid, entity_type="character", name="苏幼白"),
            Entity(project_id=pid, entity_type="item", name="玉佩·寒字七十二"),
            Entity(project_id=pid, entity_type="location", name="断魂崖"),
        ]
        for e in entities_to_add:
            db.add(e)
        db.flush()
        for n in range(1, fx.chapter_count + 1):
            db.add(Fact(
                project_id=pid,
                entity_id=entities_to_add[0].id,
                fact_type="state",
                content=f"第 {n} 章事实:陆辰丹田裂纹状态稳定",
                valid_from=n,
                valid_until=None,
            ))
        db.commit()
    with SessionLocal() as db:
        rows = (
            db.query(Chapter)
            .filter(Chapter.project_id == pid)
            .order_by(Chapter.chapter_number)
            .all()
        )
        bible = {
            "entities": db.query(Entity).filter_by(project_id=pid).count(),
            "facts": db.query(Fact).filter_by(project_id=pid).count(),
            "foreshadowings": 0,
        }
        usage_rows = db.query(LlmUsage).filter(LlmUsage.id > usage_before).all()
        usage = {
            "calls": len(usage_rows),
            "prompt_tokens": sum(int(r.prompt_tokens or 0) for r in usage_rows),
            "completion_tokens": sum(int(r.completion_tokens or 0) for r in usage_rows),
        }

    from app.evals.metrics import cross_chapter_metrics
    manifest = prompt_manifest()
    run: dict = {
        "schema": 1,
        "label": "baseline",
        "fixture": fx.name,
        "fixture_title": fx.title,
        "started_at": started.isoformat(timespec="seconds"),
        "seconds": round(time.time() - t_run, 1),
        "git_commit": git_commit(),
        "models": {
            "quality": {"model": "mock-eval", "host": "fixture", "has_key": True},
            "fast": {"model": "mock-eval", "host": "fixture", "has_key": True},
            "review": {"model": "mock-eval", "host": "fixture", "has_key": True},
        },
        "project_id": pid,
        "project_settings": {
            "review_pass_threshold": fx.review_threshold,
            "target_words": fx.target_words,
        },
        "prompt_fingerprint": manifest_fingerprint(manifest),
        "prompt_manifest": manifest,
        "chapters": records,
        "cross": cross_chapter_metrics([chapters_text[k] for k in sorted(chapters_text)]),
        "bible": bible,
        "usage": usage,
    }
    run["aggregate"] = aggregate(run)
    run["_chapter_texts"] = chapters_text
    EVALS_OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"baseline-{fx.name}-{stamp}"
    json_path = EVALS_OUT / f"{base}.json"
    text_path = EVALS_OUT / f"{base}.chapters.md"
    text_path.write_text(
        f"# 黄金样本 #1 {fx.title} · `{run['label']}`(fake baseline,非真 LLM)\n\n"
        + "\n".join(
            f"\n## 第 {n} 章 {fx.outlines[n - 1]['title']}\n\n{chapters_text[n]}\n"
            for n in sorted(chapters_text)
        ),
        encoding="utf-8",
    )
    run.pop("_chapter_texts", None)
    json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fake-baseline] 落盘 {json_path}")
    print(f"[fake-baseline] 报告:\n{evals_report.run_markdown(run)}")
    print(f"[fake-baseline] 与自己对比(同一份):")
    print(evals_report.compare_markdown(run, run))
    return json_path


if __name__ == "__main__":
    sys.exit(0 if build_fake_baseline() else 1)
