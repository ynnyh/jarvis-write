# backend/scripts/sanitize_style_memo.py
# -*- coding: utf-8 -*-
"""清掉文风备忘里的"签名句复读"(运维一次性/可重复执行)。

背景:模型把某句有画面感的短句当成"本书复现意象"沉淀进 style_memo,后续每章
生成都逐字照抄("水流冲过他的手指,凉凉的,有点麻。"反复出现)。生成侧已通过
句子级查重 + 硬规则 + 备忘新规自愈;这里清**存量** memo 里已存的复读原句,
避免前面章节继续污染后续章节。

做法:
  - 对每本书:~~全部章节正文叠加~~ 跨章统计逐字重复的分句(复用
    repetition.find_repeated_sentences)
  - 把每个命中的原句从 style_memo 里整段剔除(连中间的标点/空白一起),
    再归一化残留的标点碎片,尽量不留残缺
  - 只改 style_memo,绝不碰章节正文(改正文属破坏性操作,交给人工/重写)

用法:
    cd backend
    .venv/Scripts/python -m scripts.sanitize_style_memo --dry-run   # 预览,不改库(默认)
    .venv/Scripts/python -m scripts.sanitize_style_memo --apply     # 真改
    .venv/Scripts/python -m scripts.sanitize_style_memo --project 3 --apply  # 只处理某项目

退出码 0 = 成功。
"""
from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models.project import Project
from app.db.models.chapter import Chapter
from app.engines.consistency.repetition import find_repeated_sentences

# 清洗 memo 时视为"装饰标点",作为句子锚定位时会被跳过、剔除时连标点一起带走
_PUNCT_SET = set("，,、。；;！？!?:：·…．\"'“”‘’()（）《》「」【】[]—")

# 剔除后残留的标点碎片归一:(嵌套括号拆空/连续分隔符/首尾赘余分隔符)
_ARTIFACT_RULES = [
    (r"（\s*）", ""),           # 拆空的中文括号
    (r"\(\s*\)", ""),           # 拆空的英文括号
    (r"([：:])\s*[。！？!?；;]+", r"\1"),  # 冒号后残留的句末标点(剔除跨度没带走)
    (r"[，,、](\s*[，,、])+", "，"),  # 连续分隔符归一成单个中文逗号
    (r"[，,、](\s*[)）])", r"\1"),      # 括号前的多余分隔符
    (r"(^|[：:（(])[，,、]+", r"\1"),  # 行首/括号后多余分隔符
]


def _stripped_indices(memo: str) -> tuple[str, list[int]]:
    """去掉装饰标点与空白,返回 (纯文字stripped, 每个保留字符在原memo的下标)。"""
    chars, indices = [], []
    for i, ch in enumerate(memo):
        if ch in _PUNCT_SET or ch.isspace():
            continue
        chars.append(ch)
        indices.append(i)
    return "".join(chars), indices


def _remove_one(memo: str, key: str) -> str:
    """从 memo 里剔除第一处"纯文字==key"的区间(标点/空白连带一起删)。找不到返回原样。"""
    if not key:
        return memo
    stem, idx = _stripped_indices(memo)
    start = stem.find(key)
    if start < 0:
        return memo
    span = idx[start : start + len(key)]
    rm = set(range(span[0], span[-1] + 1))
    return "".join(ch for i, ch in enumerate(memo) if i not in rm)


def _remove_all(memo: str, key: str) -> str:
    while True:
        nxt = _remove_one(memo, key)
        if nxt == memo:
            return memo
        memo = nxt


def _clean_artifacts(memo: str) -> str:
    for pat, rep in _ARTIFACT_RULES:
        memo = re.sub(pat, rep, memo)
    # 保持 memo 的换行结构(各小节一行),只拍平行内多余空格、压掉连续空行
    memo = re.sub(r"[ \t]{2,}", " ", memo)
    memo = re.sub(r"\n{3,}", "\n\n", memo)
    return memo.strip("，,、; ").strip()


def sanitize_book(db, project_id: int) -> dict:
    """处理一本书,返回统计。"""
    proj = db.scalar(select(Project).where(Project.id == project_id))
    memo = (proj.style_memo or "").strip()
    if not memo:
        return {"id": project_id, "title": proj.title, "memo": False,
                "changed": False}
    texts = [
        c.final_content
        for c in db.scalars(
            select(Chapter)
            .where(Chapter.project_id == project_id, Chapter.final_content != "")
        ).all()
    ]
    repeated = [s for s, _ in find_repeated_sentences(texts)] if texts else []
    if not repeated:
        return {"id": project_id, "title": proj.title, "memo": True,
                "repeated": [], "changed": False}
    scrubbed = memo
    removed: list[str] = []
    for s in repeated:
        before = scrubbed
        scrubbed = _remove_all(scrubbed, s)
        if scrubbed != before:
            removed.append(s)
    scrubbed = _clean_artifacts(scrubbed)
    changed = scrubbed != memo and removed
    if changed:
        proj.style_memo = scrubbed
    return {"id": project_id, "title": proj.title, "memo": True,
            "repeated": removed, "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description="清理文风备忘里的复读原句")
    parser.add_argument("--project", type=int, default=None, help="只处理指定项目id(默认全部)")
    parser.add_argument("--apply", action="store_true", help="真正写库;缺省为 dry-run 预览")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.project is not None:
            p = db.scalar(select(Project).where(Project.id == args.project))
            rows = [sanitize_book(db, p.id)] if p else []
            if not p:
                print(f"项目 {args.project} 不存在")
                return 1
        else:
            projects = db.scalars(
                select(Project).where(Project.style_memo.isnot(None))
            ).all()
            rows = [sanitize_book(db, p.id) for p in projects]
        if args.apply:
            db.commit()

        changed = [r for r in rows if r["changed"]]
        print(f"共扫 {len(rows)} 本书的文风备忘,需清理 {len(changed)} 本,删原句 {sum(len(r['repeated']) for r in changed)} 处。")
        for r in changed:
            print(f"  [{r['id']}]《{r['title']}》删 {len(r['repeated'])} 处:")
            for s in r["repeated"]:
                print(f"      - {s}")
        if not args.apply:
            print("\n(此为预览,dry-run 未写库;确认无误加 --apply 生效)")
    return 0


if __name__ == "__main__":
    sys.exit(main())