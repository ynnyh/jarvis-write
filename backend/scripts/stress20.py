# backend/scripts/stress20.py
# -*- coding: utf-8 -*-
"""P0-3 · 20 章真实压测:验证"长程一致性"承诺。

全自动:建项目 → 架构 → 蓝图 → 逐章生成 20 章。
每章记录:耗时/字数/一致性问题数/抽取统计/AI味指数,写入
scripts/stress_report.jsonl(逐行 JSON,可随时查看进度)与最终汇总。

注意:本脚本绕过 API 直接写 DATABASE_URL 指向的库,每运行一次就新建一个
"压测·灯下黑"项目(不去重),请勿对生产库运行;跑完记得清理测试项目。

用法: .venv/Scripts/python -m scripts.stress20 [章数,默认20]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

REPORT = Path(__file__).parent / "stress_report.jsonl"


def log_line(obj: dict) -> None:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(json.dumps(obj, ensure_ascii=False)[:180])


async def main(n_chapters: int) -> int:
    from app.db.session import session_scope
    from app.db.models import Project
    from app.engines.pipeline.architecture import generate_architecture, save_architecture
    from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
    from app.engines.pipeline.chapter import generate_chapter
    from app.engines.polish.ai_flavor import ai_flavor_report

    tendency = {
        "genre": "悬疑推理", "pace": "快节奏爽文", "structure": "单线推进",
        "tone": ["悬疑", "暗黑"],
    }

    log_line({"event": "start", "chapters": n_chapters})

    with session_scope() as db:
        project = Project(
            title="压测·灯下黑",
            topic="小城台风夜,殡仪馆化妆师发现今晚送来的死者和三年前她亲手化妆下葬的是同一个人",
            genre="悬疑推理",
            target_chapters=n_chapters,
            target_words_per_chapter=2000,
            global_tendency=tendency,
        )
        db.add(project)
        db.flush()
        pid = project.id

        t0 = time.time()
        arch = await generate_architecture(
            topic=project.topic, genre=project.genre,
            number_of_chapters=n_chapters, word_number=2000,
            global_tendency=tendency,
        )
        save_architecture(db, project, arch)
        log_line({"event": "architecture", "seconds": round(time.time() - t0)})

        t0 = time.time()
        chapters, warnings = await generate_blueprint(
            novel_architecture=arch.full_text,
            number_of_chapters=n_chapters,
            global_tendency=tendency,
        )
        save_blueprint(db, project, chapters)
        log_line({
            "event": "blueprint", "seconds": round(time.time() - t0),
            "chapters": len(chapters), "warnings": warnings,
        })

    # 逐章:每章独立会话,失败不中断后续
    ok_count = 0
    for n in range(1, n_chapters + 1):
        t0 = time.time()
        try:
            with session_scope() as db:
                project = db.get(Project, pid)
                chapter, issues, stats, _guard = await generate_chapter(db, project, n)
                flavor = ai_flavor_report(chapter.final_content)
                log_line({
                    "event": "chapter", "n": n,
                    "seconds": round(time.time() - t0),
                    "words": chapter.word_count,
                    "issues": len(issues),
                    "issue_briefs": [i.get("description", "")[:60] for i in issues][:3],
                    "extract": stats.get("bible", {}),
                    "foreshadow": stats.get("foreshadow", {}),
                    "ai_flavor": flavor.score,
                })
                ok_count += 1
        except Exception as exc:  # noqa: BLE001 — 单章失败记录后继续
            log_line({
                "event": "chapter_error", "n": n,
                "seconds": round(time.time() - t0), "error": str(exc)[:300],
            })

    # 汇总
    with session_scope() as db:
        from app.db.models import Chapter, Entity, Fact, Foreshadowing
        words = sum(
            c.word_count for c in db.query(Chapter).filter_by(project_id=pid)
        )
        log_line({
            "event": "done", "project_id": pid,
            "chapters_ok": ok_count, "total_words": words,
            "entities": db.query(Entity).filter_by(project_id=pid).count(),
            "facts": db.query(Fact).filter_by(project_id=pid).count(),
            "foreshadowings": db.query(Foreshadowing).filter_by(project_id=pid).count(),
        })
    return 0 if ok_count == n_chapters else 1


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    sys.exit(asyncio.run(main(n)))
