# backend/scripts/redteam_consistency.py
# -*- coding: utf-8 -*-
"""一致性引擎召回率红队测试(真实 LLM,需配好 key)。

动机:此前一致性引擎的证据都是"抓到了 X 矛盾"这种正例,漏检率没人量化。
本脚本用人工构造的"已知答案"数据集,把核心卖点从"有案例"升级到"有数字"。

做法:
  1. 往圣经里灌一批确定的硬事实(受伤/所持/位置/关系/认知/世界观规则)。
  2. 写一批正文片段,每片段要么"明确违反"某条事实(注入矛盾,标注 golden),
     要么"完全自洽"(clean,不该报警)。
  3. 对每个片段真跑 check_chapter,统计:
       - 召回率  = 命中的矛盾数 / 注入的矛盾总数(漏检的反面)
       - 误报率  = clean 片段被报警的比例(吹毛求疵的反面)
  4. 判定命中:检查器返回的 issues 里,有任一条的 type/描述指向被违反的那条事实。
     用关键词锚点(anchor)做宽松匹配——只要 issue 文本里出现该矛盾的核心词即算命中。

用法(在服务器容器内或本地配好 key 后):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python -m scripts.redteam_consistency
    # 指定用户上下文(多用户环境,复用某账号的 key):
    REDTEAM_USER_ID=1 ... python -m scripts.redteam_consistency
"""
from __future__ import annotations

import asyncio
import os
import sys

# ---------- 数据集:圣经硬事实 ----------
# 每条 (entity, entity_type, fact_type, content, importance)
SEED_FACTS = [
    ("周衍", "character", "state", "右眼在爆炸中失明,只能靠左眼视物", "critical"),
    ("周衍", "character", "possession", "随身佩戴的青铜怀表已在第4章沉入河底遗失", "major"),
    ("苏槿", "character", "ability", "从未学过驾驶,不会开车", "major"),
    ("苏槿", "character", "location", "自第3章起被软禁在城郊别墅,不得外出", "critical"),
    ("林陌", "character", "state", "在第5章已死亡,死于枪伤", "critical"),
    ("赵会长", "character", "possession", "左手小指缺失,是二十年前赌债的抵偿", "major"),
]
# 关系事实:(from, to, relation)
SEED_RELATIONS = [
    ("周衍", "苏槿", "同父异母的兄妹,彼此知情"),
]
# 认知事实:谁知道什么(reader/角色)
# (fact_content, knower, state) —— 用于 knowledge 维度矛盾
SEED_KNOWLEDGE = [
    ("苏槿的真实身份是财团继承人", "周衍", "unknown"),  # 周衍此刻还不知道
]

ENTITY_TYPES = {e[0]: e[1] for e in SEED_FACTS}

# ---------- 数据集:测试片段 ----------
# kind="violate":注入矛盾,anchors=命中判定关键词(issue 文本含任一即算抓到)
# kind="clean"  :完全自洽,不该报警
CASES = [
    # 1. 状态:失明的眼睛"看得一清二楚"
    dict(
        id="V1-state-eye", kind="violate", dim="state",
        chars=["周衍"],
        text="周衍站在楼顶，用他那双锐利的眼睛把整条街扫视了一遍，"
             "远处巷口一个黑影的动作都被他右眼精准捕捉，纤毫毕现。"
             "他冷笑一声，转身下楼。",
        anchors=["右眼", "失明", "眼睛", "看"],
    ),
    # 2. 所持:遗失的怀表又拿出来看
    dict(
        id="V2-possession-watch", kind="violate", dim="state",
        chars=["周衍"],
        text="周衍从怀里掏出那只青铜怀表，"
             "指腹摩挲着表盖上熟悉的刻痕，表针指向凌晨三点。"
             "他把怀表收好，快步走进雨里。",
        anchors=["怀表", "遗失", "沉入", "河底"],
    ),
    # 3. 能力:不会开车的人却熟练驾驶
    dict(
        id="V3-ability-drive", kind="violate", dim="state",
        chars=["苏槿"],
        text="苏槿跳上驾驶座，熟练地挂挡、踩下油门，"
             "车子在盘山公路上漂移过弯，她眼神冷静，双手稳稳控着方向盘，"
             "一路把追兵甩得无影无踪。",
        anchors=["驾驶", "开车", "不会", "学过"],
    ),
    # 4. 空间/位置:被软禁的人却出现在另一座城市
    dict(
        id="V4-location-confined", kind="violate", dim="timeline",
        chars=["苏槿"],
        text="这天下午，苏槿独自走在千里之外的海港城老街上，"
             "买了一支冰淇淋，沿着栈桥慢慢逛，海风把她的头发吹得凌乱，"
             "没有人跟着她，她自由自在。",
        anchors=["软禁", "别墅", "外出", "城郊", "不得"],
    ),
    # 5. 认知/生死:已死的人开口说话
    dict(
        id="V5-state-dead", kind="violate", dim="state",
        chars=["林陌"],
        text="林陌推门而入，笑着拍了拍周衍的肩：“想我了吧？”"
             "他给自己倒了杯酒，一饮而尽，唇角还带着那股熟悉的痞气。",
        anchors=["林陌", "死亡", "已死", "枪伤"],
    ),
    # 6. 关系:互相知情的兄妹却被写成初次见面的陌生人
    dict(
        id="V6-relation-siblings", kind="violate", dim="knowledge",
        chars=["周衍", "苏槿"],
        text="周衍第一次见到苏槿，礼貌地伸出手：“你好，请问我们以前见过吗？”"
             "苏槿摇头，两个陌生人客气地交换了名片，彼此毫无干系。",
        anchors=["兄妹", "同父异母", "知情", "关系"],
    ),
    # 7. 认知:角色说出他此刻不该知道的信息
    dict(
        id="V7-knowledge", kind="violate", dim="knowledge",
        chars=["周衍", "苏槿"],
        text="周衍盯着苏槿，一字一句地说：“我早就查清楚了，"
             "你就是那个财团继承人，这一切都是你在背后操控。”"
             "苏槿脸色骤变。",
        anchors=["继承人", "身份", "不该知道", "得知"],
    ),
    # 8. 细节:缺失小指的手却"十指健全"
    dict(
        id="V8-detail-finger", kind="violate", dim="state",
        chars=["赵会长"],
        text="赵会长十指交叉搁在桌上，左手小指上那枚翡翠戒指格外扎眼，"
             "他慢条斯理地转动着戒指，笑意不达眼底。",
        anchors=["小指", "缺失", "左手"],
    ),
    # 9. 世界观:直接用一条被设定否定的能力(把失明当没发生地贯穿全场景)
    dict(
        id="V9-state-eye-2", kind="violate", dim="state",
        chars=["周衍"],
        text="夜色中周衍眯起双眼，借着微光读完了那封密信上所有蝇头小字，"
             "两只眼睛都因专注而微微发酸。读罢，他把信纸凑到烛火上点燃。",
        anchors=["右眼", "失明", "双眼", "两只眼"],
    ),
    # ----- clean:完全自洽,不该报警 -----
    dict(
        id="C1-clean-eye", kind="clean", dim="state",
        chars=["周衍"],
        text="周衍偏过头，用尚存视力的左眼打量着来人，"
             "右眼那道旧疤在灯下泛着淡淡的白。他沉默了很久，才缓缓开口。",
        anchors=[],
    ),
    dict(
        id="C2-clean-confined", kind="clean", dim="location",
        chars=["苏槿"],
        text="苏槿趴在别墅二楼的窗台上，望着院墙外看不见的远方，"
             "守卫的身影在花园里来回巡逻。她叹了口气，退回房间。",
        anchors=[],
    ),
    dict(
        id="C3-clean-generic", kind="clean", dim="none",
        chars=["赵会长"],
        text="赵会长端起茶盏抿了一口，用右手把一份文件推到对面，"
             "缺了小指的左手安静地压在膝上。“这笔生意，我接了。”",
        anchors=[],
    ),
]


def _hit(issues: list[dict], anchors: list[str]) -> bool:
    """issue 文本(description + conflicting_fact + suggestion)含任一 anchor 即算命中。"""
    blob = " ".join(
        f"{i.get('description','')} {i.get('conflicting_fact','')} {i.get('type','')} {i.get('suggestion','')}"
        for i in issues
    )
    return any(a in blob for a in anchors)


async def _seed(db, project_id: int):
    from app.engines.consistency import BibleService

    bible = BibleService(db, project_id)
    # 灌事实:每条从其 valid_from 起生效。为让所有事实在测试章(第9章)都有效,
    # state/possession 类的 valid_from 用 3~5,测试统一按第 9 章检查。
    changes = [
        {"entity": e, "fact_type": ft, "content": c, "importance": imp, "replaces": None}
        for (e, _t, ft, c, imp) in SEED_FACTS
    ]
    rels = [
        {"entity": a, "fact_type": "relationship", "content": r,
         "other_entity": b, "importance": "major", "replaces": None}
        for (a, b, r) in SEED_RELATIONS
    ]
    bible.apply_extraction(3, {"fact_changes": changes + rels})
    db.commit()


async def main() -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Project
    from app.engines.consistency.checker import check_chapter

    # 多用户环境:允许用 REDTEAM_USER_ID 复用某账号的 key
    uid = os.environ.get("REDTEAM_USER_ID")
    if uid:
        from app.auth import current_user_id
        current_user_id.set(int(uid))

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    p = Project(title="红队", topic="一致性红队", genre="悬疑",
                target_chapters=20, target_words_per_chapter=2000)
    db.add(p)
    db.commit()
    await _seed(db, p.id)

    from app.engines.consistency import BibleService
    bible = BibleService(db, p.id)
    print("=== 已灌入圣经硬约束(第9章视角) ===")
    print(bible.hard_constraints_block(9, [e[0] for e in SEED_FACTS]))
    print("=" * 60)

    CHECK_AT = 9
    violate_total = 0
    violate_hit = 0
    clean_total = 0
    clean_false_alarm = 0
    rows = []

    for case in CASES:
        issues = await check_chapter(
            db, p.id, CHECK_AT, case["text"], rolling_summary=""
        )
        n_issues = len(issues)
        if case["kind"] == "violate":
            violate_total += 1
            hit = _hit(issues, case["anchors"])
            violate_hit += 1 if hit else 0
            verdict = "命中✓" if hit else "漏检✗"
            rows.append((case["id"], case["dim"], verdict, n_issues))
        else:
            clean_total += 1
            false_alarm = n_issues > 0
            clean_false_alarm += 1 if false_alarm else 0
            verdict = "误报✗" if false_alarm else "干净✓"
            rows.append((case["id"], case["dim"], verdict, n_issues))
        # 打印每条 issue 摘要,便于人工核验命中/误报的合理性
        for it in issues:
            print(f"    · [{case['id']}] {it.get('type','?')}/{it.get('severity','?')}: "
                  f"{it.get('description','')[:80]}")

    print("=" * 60)
    print(f"{'CASE':<22}{'维度':<10}{'结果':<8}{'issues'}")
    for cid, dim, verdict, n in rows:
        print(f"{cid:<22}{dim:<10}{verdict:<8}{n}")
    print("=" * 60)
    recall = violate_hit / violate_total if violate_total else 0.0
    fp_rate = clean_false_alarm / clean_total if clean_total else 0.0
    print(f"召回率(抓到的矛盾/注入的矛盾): {violate_hit}/{violate_total} = {recall:.0%}")
    print(f"误报率(clean 被报警/clean 总数): {clean_false_alarm}/{clean_total} = {fp_rate:.0%}")
    print("=" * 60)
    # 门槛:召回 >= 80% 且误报 <= 33%(clean 3 条允许最多误报 1 条)才算通过
    ok = recall >= 0.8 and fp_rate <= 0.34
    print("红队结论:", "通过 ✅" if ok else "未达标 ⚠️（见上方漏检/误报明细）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
