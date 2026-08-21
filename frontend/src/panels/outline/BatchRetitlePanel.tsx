// 批量重拟标题面板(Pillar 3:一键换一批标题,不动剧情)。
// 选风格 → AI 为全书各章重拟 → old→new 预览(可逐条编辑/勾选/再换一批)→ 应用。
// 应用只改 title(cosmetic),已写正文不会被标记失配。
import { useState } from "react";
import { api, RetitleItem } from "../../api";
import { errMsg } from "../../pollJob";
import TitleStyleControl, { DEFAULT_TITLE_STYLE, TitleStyle } from "../../components/TitleStyleControl";

interface Props {
  pid: number;
  totalChapters: number;
  onApplied: (msg: string) => void; // 应用成功:由父组件刷新数据 + flash + 关闭
  onClose: () => void;
}

export default function BatchRetitlePanel({ pid, totalChapters, onApplied, onClose }: Props) {
  const [ts, setTs] = useState<TitleStyle>(DEFAULT_TITLE_STYLE);
  const [items, setItems] = useState<RetitleItem[] | null>(null); // null=还没生成
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  async function run() {
    setBusy(items ? "再换一批标题…" : "AI 正在为全书重拟标题…"); setErr("");
    try {
      const r = await api.retitleAllChapters(pid, ts.style, ts.directive);
      setItems(r.items);
      setPicked(new Set(r.items.map((i) => i.chapter_number))); // 默认全选
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  function editTitle(n: number, v: string) {
    setItems((cur) => cur && cur.map((it) => (it.chapter_number === n ? { ...it, new_title: v } : it)));
  }
  function togglePick(n: number, on: boolean) {
    setPicked((cur) => { const s = new Set(cur); if (on) s.add(n); else s.delete(n); return s; });
  }

  async function apply() {
    if (!items) return;
    const chosen = items
      .filter((it) => picked.has(it.chapter_number) && it.new_title.trim())
      .map((it) => ({ chapter_number: it.chapter_number, new_title: it.new_title.trim() }));
    if (!chosen.length) return;
    setBusy("应用新标题…"); setErr("");
    try {
      const r = await api.applyRetitleAll(pid, chosen);
      onApplied(`已更新第 ${r.updated.join("、")} 章标题(未改动剧情,正文不受影响)`);
    } catch (e) { setErr(errMsg(e)); setBusy(""); }
  }

  const chosenCount = items ? items.filter((it) => picked.has(it.chapter_number) && it.new_title.trim()).length : 0;

  return (
    <div className="mt-3">
      <div className="muted">
        为全书 {totalChapters} 章统一换一批标题——只改标题,不动剧情简述,已写正文不会失配。先选风格:
      </div>
      <TitleStyleControl value={ts} onChange={setTs} compact />

      {!items && (
        <div className="actions mt-2">
          <button className="primary" disabled={!!busy} onClick={run}>
            {busy && <span className="spin" />}生成新标题
          </button>
          <button disabled={!!busy} onClick={onClose}>取消</button>
        </div>
      )}

      {items && items.length === 0 && (
        <div className="card card-ok mt-3">
          <b>AI 认为现有标题已经合适</b>
          <div className="muted mt-1">没有建议修改的章节。可换个风格「再换一批」试试。</div>
          <div className="actions mt-2">
            <button className="primary" disabled={!!busy} onClick={run}>
              {busy && <span className="spin" />}再换一批
            </button>
            <button disabled={!!busy} onClick={onClose}>关闭</button>
          </div>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="card card-warn mt-3">
          <div className="fl">
            <b>新标题预览({items.length} 章有改动)</b>
            <span className="grow" />
            <button className="btn-sm" onClick={() => setPicked(new Set(items.map((i) => i.chapter_number)))}>全选</button>
            <button className="btn-sm" onClick={() => setPicked(new Set())}>全不选</button>
          </div>
          <div className="muted mt-1">勾选要采用的;新标题可直接改。</div>
          {items.map((it) => (
            <div key={it.chapter_number} className="fact-line fact-check">
              <input
                type="checkbox"
                checked={picked.has(it.chapter_number)}
                onChange={(e) => togglePick(it.chapter_number, e.target.checked)}
              />
              <div className="grow">
                <b>第{it.chapter_number}章</b>{" "}
                <span className="muted">{it.old_title || "(无标题)"}</span> →
                <div className="input-row mt-1">
                  <input
                    type="text"
                    value={it.new_title}
                    onChange={(e) => editTitle(it.chapter_number, e.target.value)}
                  />
                </div>
              </div>
            </div>
          ))}
          <div className="actions mt-2">
            <button className="primary" disabled={!!busy || !chosenCount} onClick={apply}>
              {busy && <span className="spin" />}采用选中({chosenCount} 章)
            </button>
            <button disabled={!!busy} onClick={run}>
              {busy === "再换一批标题…" && <span className="spin" />}再换一批
            </button>
            <button disabled={!!busy} onClick={onClose}>取消</button>
          </div>
        </div>
      )}

      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
