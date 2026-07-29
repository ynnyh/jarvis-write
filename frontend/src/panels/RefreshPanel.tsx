// 翻新:让已有书按新生成逻辑(节拍/文风备忘/去AI味)重构
// 四件套:回填节拍 → 初始化文风备忘 → 轻度重润(锁情节) / 重度重写(重跑生成)
import { useEffect, useState } from "react";
import { api, ChapterBrief } from "../api";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";

interface Props { pid: number; }

interface ChapterFailure { chapter: number; error?: string; }
interface HeavyResult {
  rewritten: number[]; total: number; stopped_at: number | null;
  remaining: number[]; error: string | null;
}

export default function RefreshPanel({ pid }: Props) {
  const { run: runJob } = useJob();
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [memo, setMemo] = useState<string>("");
  // 失败后的一键补救:轻度重润/回填的失败章、重度重写的剩余章(进度后端已按章保存)
  const [lightFailed, setLightFailed] = useState<number[]>([]);
  const [heavyRemaining, setHeavyRemaining] = useState<number[]>([]);

  function loadChapters() {
    api.listChapters(pid)
      .then((list) => setChapters(list.filter((c) => c.status !== "empty")))
      .catch((e) => setErr(String(e)));
  }
  useEffect(() => { loadChapters(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const written = chapters.map((c) => c.chapter_number);
  const allPicked = written.length > 0 && written.every((n) => picked.has(n));

  function toggle(n: number, on: boolean) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(n); else next.delete(n);
      return next;
    });
  }
  function toggleAll(on: boolean) {
    setPicked(on ? new Set(written) : new Set());
  }
  const selected = () => Array.from(picked).sort((a, b) => a - b);

  async function doJob<T>(
    label: string,
    start: () => Promise<{ job_id: string }>,
    done: (r: T) => void,
  ) {
    setErr(""); setBusy(label); setStage("");
    try {
      const r = await runJob<T>(start, { kind: label, onStage: setStage });
      // 完成后刷新章节列表(状态/字数/失配标记可能已变)
      if (r) { done(r); loadChapters(); }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(""); setStage("");
    }
  }

  const nums = () => (picked.size ? selected() : []); // 空 = 全书(后端展开)

  const runBackfill = (list: number[]) =>
    doJob<{ filled: number[]; skipped: number[]; failed: ChapterFailure[] }>(
      "回填节拍",
      () => api.refreshBackfillBeats(pid, list),
      (r) => {
        const failedNums = (r.failed || []).map((f) => f.chapter);
        if (failedNums.length)
          toast.err(`回填完成 ${r.filled.length} 章`, `失败:第 ${failedNums.join("、")} 章,可重新回填`);
        else
          toast.ok(`回填完成:${r.filled.length} 章,跳过 ${r.skipped.length} 章`);
      },
    );

  const runSeedMemo = () =>
    doJob<{ style_memo: string; seeded: boolean; existed: boolean }>(
      "初始化文风备忘",
      () => api.refreshSeedStyleMemo(pid),
      (r) => {
        setMemo(r.style_memo || "");
        if (r.seeded) toast.ok("文风备忘已生成");
        else if (r.existed) toast.info("已有文风备忘,未覆盖");
        else toast.err("文风备忘生成失败", "没有可扫描的正文,或上游调用失败,请稍后重试");
      },
    );

  const runLight = (list: number[]) =>
    doJob<{ refreshed: number[]; failed: ChapterFailure[]; total: number }>(
      "轻度重润",
      () => api.refreshLight(pid, list),
      (r) => {
        const failedNums = (r.failed || []).map((f) => f.chapter);
        setLightFailed(failedNums);
        if (failedNums.length)
          toast.err(`重润完成 ${r.refreshed.length}/${r.total} 章`,
            `失败:第 ${failedNums.join("、")} 章,可点下方按钮重试`);
        else
          toast.ok(`重润完成:${r.refreshed.length}/${r.total} 章`);
      },
    );

  const runHeavy = (list: number[]) => {
    if (!confirm("重度重写会覆盖选中章节的正文(有快照可回滚),确定继续?")) return;
    doJob<HeavyResult>(
      "重度重写",
      () => api.refreshHeavy(pid, list),
      (r) => {
        setHeavyRemaining(r.remaining || []);
        if (r.error)
          toast.err(r.error,
            `已完成 ${r.rewritten.length}/${r.total} 章,进度已保存,可续跑剩余 ${r.remaining.length} 章`);
        else
          toast.ok(`重写完成:${r.rewritten.length}/${r.total} 章`);
      },
    );
  };

  return (
    <div className="refresh-panel">
      <div className="card mb-3">
        <b>翻新已有书</b>
        <div className="card-desc mt-1">
          用升级后的生成逻辑(场景节拍 / 文风备忘 / 去 AI 味)重构已经写好的章节。
          建议顺序:先<b>回填节拍</b>和<b>初始化文风备忘</b>打好基础,再按需<b>轻度重润</b>或<b>重度重写</b>。
        </div>
      </div>

      {/* 章节选择 */}
      <div className="card mb-3">
        <div className="row-between">
          <b>选择章节</b>
          <label className="muted">
            <input type="checkbox" checked={allPicked} onChange={(e) => toggleAll(e.target.checked)} />
            全选({written.length} 章已成文)
          </label>
        </div>
        <div className="chapter-pick mt-2">
          {chapters.length === 0 && <span className="muted">还没有已成文的章节。</span>}
          {chapters.map((c) => (
            <label key={c.chapter_number}
              className={"pick-chip" + (picked.has(c.chapter_number) ? " on" : "")}>
              <input type="checkbox" checked={picked.has(c.chapter_number)}
                onChange={(e) => toggle(c.chapter_number, e.target.checked)} />
              第{c.chapter_number}章
            </label>
          ))}
        </div>
        <div className="hint mt-1">
          不勾选 = 对全书执行(回填节拍作用于全部大纲,重润/重写作用于全部已成文章)。
        </div>
      </div>

      {err && <div className="msg-err mb-2">{err}</div>}
      {busy && <div className="msg-info mb-2"><span className="spin" />{busy}{stage ? ` · ${stage}` : ""}</div>}

      {/* 四件套 */}
      <div className="refresh-actions">
        <div className="card action-card">
          <b>① 回填场景节拍</b>
          <div className="card-desc">为大纲补出每章 3-5 个场景节拍(重度重写按此铺场景)。只改大纲,不动正文。</div>
          <button className="btn-sm" disabled={!!busy}
            onClick={() => runBackfill(nums())}>
            回填节拍
          </button>
        </div>

        <div className="card action-card">
          <b>② 初始化文风备忘</b>
          <div className="card-desc">扫前几章正文,生成"这本书怎么写"的文风基准,注入后续生成。已有则不覆盖。</div>
          <button className="btn-sm" disabled={!!busy} onClick={runSeedMemo}>
            生成文风备忘
          </button>
          {memo && (
            <details className="mt-2">
              <summary className="muted">查看文风备忘</summary>
              <pre className="memo-pre">{memo}</pre>
            </details>
          )}
        </div>

        <div className="card action-card">
          <b>③ 轻度重润</b>
          <div className="card-desc">锁情节 + 去 AI 味,只改文字不改剧情。安全、快,不重抽圣经。会留版本快照可回滚。</div>
          <button className="btn-sm" disabled={!!busy}
            onClick={() => { setLightFailed([]); runLight(nums()); }}>
            轻度重润{picked.size ? `(${picked.size} 章)` : "(全书)"}
          </button>
          {lightFailed.length > 0 && !busy && (
            <button className="btn-sm mt-2"
              onClick={() => runLight(lightFailed)}>
              重试失败的 {lightFailed.length} 章(第 {lightFailed.join("、")} 章)
            </button>
          )}
        </div>

        <div className="card action-card warn-card">
          <b>④ 重度重写</b>
          <div className="card-desc">
            带节拍/概念/文风备忘<b>整章重跑生成</b>,正文会被覆盖(留快照可回滚),并自动重抽圣经、重建下游摘要。
            与逐章生成互斥,按章号顺序串行。中途失败时已完成的章会保留,可续跑剩余章节。
          </div>
          <button className="btn-sm danger" disabled={!!busy}
            onClick={() => { setHeavyRemaining([]); runHeavy(nums()); }}>
            重度重写{picked.size ? `(${picked.size} 章)` : "(全书)"}
          </button>
          {heavyRemaining.length > 0 && !busy && (
            <button className="btn-sm danger mt-2"
              onClick={() => runHeavy(heavyRemaining)}>
              续跑剩余 {heavyRemaining.length} 章(第 {heavyRemaining.join("、")} 章)
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
