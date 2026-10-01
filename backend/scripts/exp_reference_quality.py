"""隔离数据库的真实模型对照实验；只向 evals_out 写样本，不写用户作品。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import time


def cases():
    from app.prompts.inspire import BOOK_PLANS_PROMPT, BOOK_PLANS_SHORT_PROMPT
    from app.prompts.anime import ANIME_TAKES_PROMPT
    from app.prompts.polish import DEAI_REWRITE_PROMPT

    out = []
    for name, topic, short in [
        ("男频漫剧", "都市男频漫剧，底层维修工遭恶意索赔。要强开场、连续解气、有代价的新困境；不要最后亮身份一招摆平。", False),
        ("女频漫剧", "女频职场漫剧，女审计被迫背锅。靠专业与证据翻盘，不靠霸总拯救；要关系与利益不断升级。", False),
        ("人味连载", "县城旧车修理铺，师徒合伙熬过生意下滑，现实生活质感，不要金手指、不刻意煽情。", False),
        ("短故事", "儿子陪退休父亲清理旧仓库，在一张车票上发现家庭误会；克制、因果顺畅，不要结尾强行上价值。", True),
    ]:
        p = (BOOK_PLANS_SHORT_PROMPT if short else BOOK_PLANS_PROMPT).format(
            topic=topic, answers="按作者要求具体设计", style_directives="", skill_block="",
            genre_boundary="严守现实题材和作者明确要求。",
        )
        out.append((name, "ARCHITECTURE", p))
    out.append(("对白喜剧", "ANIME_TAKES", ANIME_TAKES_PROMPT.format(
        genre_label="搞笑", framing="日常情境，人物一本正经做荒唐事，用对白误导产生笑点，类似屌丝男士的观看体验，原创情节。",
        beats="铺垫、误导、升级、落点", premise="两个打工人爱占小便宜又要面子",
        premise_line="饭店买单", cast_block="阿成：爱面子的打工人；小吴：认真但钻规则空子；服务员阿梅：实际清醒。",
    )))
    text = "老陈伸出手。他握住了门把。他缓缓转动门把。门开了。他的心中五味杂陈。儿子站在桌前，手里拿着那张1998年去南昌的车票。\n‘你不是说没去过？’\n老陈沉默片刻。他的手指微微颤抖。他低下了头。‘去了，到站又回来了。你奶奶那天进医院。’儿子把车票放回铁盒，没有再问。"
    out.append(("正文去味", "FINALIZE", DEAI_REWRITE_PROMPT.format(
        flavor_hits="动作拆成多句、重复解释情绪、缓缓/微微/沉默片刻", advice_block="合并无信息增量的动作。", pairwise="", style_directives="现实短篇，克制自然，保留票上的时间地点与人物因果。", draft_text=text,
    )))
    return out


async def run(label: str, repeat: int, case: str):
    from app.auth import current_user_id
    from app.llm.router import Task, get_adapter_for
    from app.engines.consistency.extractor import parse_llm_json
    current_user_id.set(1)
    target = Path("evals_out/reference-quality") / label
    target.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(2)

    async def one(name, task, prompt, n):
        async with sem:
            start = time.monotonic()
            item = {"case": name, "repeat": n, "task": task, "prompt": prompt}
            try:
                item["output"] = await get_adapter_for(getattr(Task, task), max_tokens=6000, timeout=180).ask(prompt)
                item["json_ok"] = task == "FINALIZE" or isinstance(parse_llm_json(item["output"]), dict)
            except Exception as e:
                # 不记录异常中的网关地址/认证信息。
                item["error"] = type(e).__name__
                item["status"] = getattr(e, "status", None)
                detail = re.sub(r"https?://\S+|sk-[\w-]+|Bearer\s+\S+", "[redacted]", str(e))
                item["detail"] = detail[:300]
            item["seconds"] = round(time.monotonic() - start, 2)
            (target / f"{name}-{n}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in item.items() if k not in ("prompt", "output")}, ensure_ascii=False), flush=True)

    await asyncio.gather(*(one(*c, n) for c in cases() if not case or c[0] == case for n in range(1, repeat + 1)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    parser.add_argument("--case", default="")
    args = parser.parse_args()
    # 在导入应用前完成 SQLite backup，读源库的主库和 WAL 一致快照。
    with tempfile.TemporaryDirectory(prefix="jw-reference-eval-") as tmp:
        db = Path(tmp) / "eval.db"
        src = sqlite3.connect(Path("jarvis_write.db").resolve().as_uri() + "?mode=ro", uri=True)
        dst = sqlite3.connect(db)
        try:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        os.environ["DATABASE_URL"] = "sqlite:///" + db.as_posix()
        try:
            asyncio.run(run(args.label, args.repeat, args.case))
        finally:
            from app.db.session import engine
            engine.dispose()
