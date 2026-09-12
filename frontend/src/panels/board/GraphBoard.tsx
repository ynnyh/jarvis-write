// 关系图谱看板(docs/19 M4):全书人物为节点、关系边为连线,按章节时序滑块看
// 「第 N 章时」的关系状态(Relationship 自带 valid_from/until,天然支持)。
// 手写 SVG 圆形布局,不引图库;pending 边虚线标注(抽取建联待作者确认)。
import { useEffect, useMemo, useState } from "react";
import { api, CharactersOut } from "../../api";
import { errMsg } from "../../pollJob";

export default function GraphBoard({ pid, onGotoChapter }: {
  pid: number; onGotoChapter?: (n: number) => void;
}) {
  const [data, setData] = useState<CharactersOut | null>(null);
  const [err, setErr] = useState("");
  const [maxChapter, setMaxChapter] = useState(0);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    (async () => {
      setErr("");
      try {
        const out = await api.characters(pid);
        setData(out);
        const m = Math.max(
          1,
          ...out.characters.flatMap((c) => [
            ...c.appearance_chapters,
            ...c.relations.map((r) => r.valid_from),
          ]),
        );
        setMaxChapter(m);
        setCursor(m);
      } catch (e) { setErr(errMsg(e)); }
    })();
  }, [pid]);

  const nodes = useMemo(() => {
    const chars = data?.characters ?? [];
    const n = chars.length;
    const R = 170;
    const cx = 260; const cy = 260;
    return chars.map((c, i) => {
      const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
      return {
        card: c,
        x: cx + R * Math.cos(angle),
        y: cy + R * Math.sin(angle),
      };
    });
  }, [data]);

  const edges = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.card.id, n]));
    const out: { key: string; a: { x: number; y: number }; b: { x: number; y: number };
      label: string; pending: boolean }[] = [];
    for (const c of data?.characters ?? []) {
      for (const r of c.relations) {
        if (cursor < r.valid_from) continue;
        if (r.valid_until !== null && cursor >= r.valid_until) continue;
        const other = data?.characters.find((x) => x.name === r.other_name);
        const na = byId.get(c.id);
        const nb = other ? byId.get(other.id) : undefined;
        if (!na || !nb) continue;
        out.push({
          key: `${c.id}-${r.other_name}-${r.valid_from}`,
          a: { x: na.x, y: na.y }, b: { x: nb.x, y: nb.y },
          label: r.description, pending: r.status === "pending",
        });
      }
    }
    return out;
  }, [nodes, data, cursor]);

  if (err) return <div className="msg-err">{err}</div>;
  if (!data) return <div className="muted"><span className="spin" />加载人物图谱…</div>;

  return (
    <div className="card">
      <div className="card-head">
        <h2>关系图谱</h2>
        <span className="muted">
          节点=人物(按出场),连线=关系边;拖动滑块看「第 N 章时」的关系状态。虚线=抽取建联待确认。
        </span>
      </div>

      {data.characters.length < 2 ? (
        <div className="muted mt-2">
          圣经人物不足两个,还没有可画的图谱。先在「人物」或正文抽取里积累人物。
        </div>
      ) : (
        <>
          <div className="graph-slider">
            <span className="muted">第 1 章</span>
            <input type="range" min={1} max={maxChapter} value={cursor}
              onChange={(e) => setCursor(Number(e.target.value))} />
            <span className="muted">第 {maxChapter} 章</span>
            <b className="ml-2">当前:第 {cursor} 章</b>
          </div>
          <svg className="graph-svg" viewBox="0 0 520 520" role="img">
            {edges.map((e) => (
              <line key={e.key} x1={e.a.x} y1={e.a.y} x2={e.b.x} y2={e.b.y}
                className={"graph-edge" + (e.pending ? " pending" : "")}>
                <title>{e.label}{e.pending ? "(待确认)" : ""}</title>
              </line>
            ))}
            {nodes.map((n) => (
              <g key={n.card.id} className="graph-node"
                onClick={() => onGotoChapter?.(n.card.appearance_chapters[0] ?? 1)}>
                <circle cx={n.x} cy={n.y} r={16}
                  className={n.card.retired ? "retired" : ""}
                  data-entity-type={n.card.entity_type}>
                  <title>{n.card.name}:{n.card.profile || "人物"}{n.card.retired ? "(已退场)" : ""}</title>
                </circle>
                <text x={n.x} y={n.y + 30} textAnchor="middle">{n.card.name}</text>
              </g>
            ))}
          </svg>
          <div className="muted mt-2">
            点人物名旁的圆点可跳到该人物首次出场的章节;悬停连线看关系名。
          </div>
        </>
      )}
    </div>
  );
}
