import { useEffect, useState } from "react";
import type { DramaDirection, DramaDirectionRec, DramaStyleCard } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";

// ================= 美术风格卡 =================
export function StyleSection({ pid, style, directions, onSaved }: {
  pid: number;
  style: DramaStyleCard | null;
  directions: DramaDirection[];
  onSaved: (s: DramaStyleCard) => void;
}) {
  const { run } = useJob();
  const [busy, setBusy] = useState(false);
  const [recBusy, setRecBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState<DramaStyleCard | null>(style);
  const [direction, setDirection] = useState(style?.direction || "auto");
  const [recs, setRecs] = useState<DramaDirectionRec[]>([]);

  useEffect(() => { setDraft(style); }, [style]);

  const dirInfo = directions.find((d) => d.key === direction);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaStyleCard>(
        () => dramaApi.generateStyle(pid, direction),
        { kind: `drama-style-${pid}`, onStage: setStage },
      );
      if (r) { onSaved(r); toast.ok("美术风格已定", "这段画风锁定会注入每一格分镜"); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function recommend() {
    setRecBusy(true); setErr("");
    try {
      const r = await run<{ recommendations: DramaDirectionRec[] }>(
        () => dramaApi.recommendDirections(pid),
        { kind: `drama-dirrec-${pid}` },
      );
      if (r) setRecs(r.recommendations);
    } catch (e) { setErr(errMsg(e)); } finally { setRecBusy(false); }
  }

  async function save() {
    if (!draft) return;
    try {
      const r = await dramaApi.saveStyle(pid, { ...draft, direction });
      onSaved(r.style);
      toast.ok("风格卡已保存");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  function field(label: string, key: keyof DramaStyleCard, rows: number) {
    if (!draft) return null;
    return (
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">{label}</span></div>
        <textarea rows={rows} value={draft[key] as string}
          onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">① 美术风格卡 <span className="muted">全片画风统一</span></h3>
        <button className="btn-sm" disabled={recBusy} onClick={recommend}>
          {recBusy ? "推荐中…" : "AI 荐方向"}
        </button>
        {style && <button className="btn-sm" onClick={save}>保存修改</button>}
        <button className="primary" disabled={busy} onClick={generate}>
          {style ? "重新生成" : "AI 定美术风格"}
        </button>
      </div>
      <p className="card-desc">
        先拍板「画风方向」,再让 AI 在这个方向内定「画风锁定段」——之后每一条分镜提示词都会
        逐字嵌入这段锚,上百格画面画风不漂移。生成后可直接改字微调。
      </p>

      {/* 方向推荐:AI 荐、用户选,点一下即采用 */}
      {recs.length > 0 && (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>按本书气质推荐</b>
            <span className="muted">点一条即选中该方向,也可以无视推荐自己挑</span></div>
          {recs.map((r) => (
            <button key={r.key} type="button"
              className={"chip dir-rec" + (direction === r.key ? " on" : "")}
              onClick={() => setDirection(r.key)}>
              <b>{r.priority === 1 ? "★ " : ""}{r.label}</b>
              <span className="muted">{r.reason}</span>
              {r.tip && <span className="warn-tip">⚠ {r.tip}</span>}
            </button>
          ))}
        </div>
      )}

      {/* 方向选择 */}
      <div className="chips board-tabs mb-2">
        {directions.map((d) => (
          <button key={d.key} type="button"
            className={"chip" + (direction === d.key ? " on" : "")}
            onClick={() => setDirection(d.key)}>
            {d.label}
          </button>
        ))}
      </div>
      {dirInfo?.tip && <p className="hint warn-tip">⚠ {dirInfo.tip}</p>}
      {!style && !busy && (
        <p className="hint wb-next">
          <b>第一步就在这儿:</b>拿不定方向就先点「AI 荐方向」看它怎么说,
          定好后点「AI 定美术风格」——没有这张卡,后面的「出提示词」会被拦下。
        </p>
      )}

      {busy && <Banner stage={stage} text="AI 正在定全片美术风格…" />}
      {err && <div className="msg-err">{err}</div>}
      {draft && (
        <>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">风格名</span></div>
            <input value={draft.style_name}
              onChange={(e) => setDraft({ ...draft, style_name: e.target.value })} />
          </div>
          {field("画风锁定段(中文,即梦等)", "style_cn", 3)}
          {field("画风锁定段(英文,Midjourney)", "style_en", 2)}
          {field("负面词基座", "negative", 2)}
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">画幅</span></div>
            <input value={draft.ratio}
              onChange={(e) => setDraft({ ...draft, ratio: e.target.value })} />
          </div>
        </>
      )}
    </div>
  );
}
