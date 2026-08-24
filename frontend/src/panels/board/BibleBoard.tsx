// 故事圣经·时间机:查看任意章节时刻的世界状态(按实体聚合有效事实)(拆自 BoardPanel.tsx)。
import { useCallback, useEffect, useState } from "react";
import { api, BibleSnapshot, FactOut } from "../../api";
import { IMP_BADGE, Props } from "./shared";
import { errMsg } from "../../pollJob";

// 资源类事实(持有/能力)在生成侧有一份专门的「角色资源账本」闭集约束
// (后端 engines/consistency/ledger.py:不许凭空掏出、新增要交代来源、用掉要写明)。
// 这里给它们标一枚类型 badge:账本里现在挂着什么作者得看得见——
// 「三章前就该吃完的干粮还挂着」这种失真,只有人眼能一眼认出来。
const TYPE_LABEL: Record<string, string> = { possession: "持有", ability: "会/能" };

export default function BibleBoard({ pid, outlines }: Props) {
  const maxCh = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 1;
  const [atChapter, setAtChapter] = useState(maxCh);
  // 输入框用字符串保存原始输入(允许清空重输),仅在解析合法时才切换章节时刻
  const [atInput, setAtInput] = useState(String(maxCh));
  const [bible, setBible] = useState<BibleSnapshot | null>(null);
  const [err, setErr] = useState("");

  const reload = useCallback(async (ch: number) => {
    setErr("");
    try { setBible(await api.bible(pid, ch)); } catch (e) { setErr(errMsg(e)); }
  }, [pid]);

  useEffect(() => { reload(atChapter); }, [reload, atChapter]);

  const byEntity = new Map<string, FactOut[]>();
  bible?.facts.forEach((f) => {
    const list = byEntity.get(f.entity) ?? [];
    list.push(f);
    byEntity.set(f.entity, list);
  });

  return (
    <div className="card">
      <div className="card-head">
        <h2>故事圣经 · 时间机</h2>
        <span className="muted">查看任意章节时刻的世界状态</span>
        <div className="grow" />
        <span className="muted">第</span>
        <input type="number" min={1} max={maxCh} value={atInput} className="input-xs"
          onChange={(e) => {
            const v = e.target.value;
            setAtInput(v);
            const n = Number(v);
            if (v.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= maxCh) setAtChapter(n);
          }} />
        <span className="muted">章时刻 · {bible?.entities_count ?? 0} 实体 / {bible?.facts.length ?? 0} 条有效事实</span>
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}
      <div className="mt-3">
        {[...byEntity.entries()].map(([entity, facts]) => (
          <div key={entity} className="entity">
            <b>{entity}</b>
            {facts.map((f, i) => (
              <div key={i} className="fact-line">
                <span className={"badge " + (IMP_BADGE[f.importance] ?? "")}>{f.importance}</span>
                {TYPE_LABEL[f.fact_type] && <> <span className="badge">{TYPE_LABEL[f.fact_type]}</span></>}
                {" "}{f.content}
                <span className="muted">(第{f.valid_from}{f.valid_until ? `-${f.valid_until}` : " 章起"}章有效)</span>
              </div>
            ))}
          </div>
        ))}
        {!bible?.facts.length && <div className="muted">该时刻暂无已登记事实(生成章节后自动抽取)。</div>}
      </div>
    </div>
  );
}
