// 翻新:让已有书按新生成逻辑(节拍/文风备忘/去AI味)重构
// 四件套:回填节拍 → 初始化文风备忘 → 轻度重润(锁情节) / 重度重写(重跑生成)
import { useEffect, useState } from "react";
import { api, ChapterBrief } from "../api";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";

interface Props { pid: number; }

export default function RefreshPanel({ pid }: Props) {
  const { run: runJob } = useJob();
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [memo, setMemo] = useState<string>("");

  useEffect(() => {
    api.listChapters(pid)
      .then((list) => setChapters(list.filter((c) => c.status !== "empty")))
      .catch((e) => setErr(String(e)));
  }, [pid]);

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
      if (r) done(r);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(""); setStage("");
    }
  }

  const nums = () => (picked.size ? selected() : []); // 空 = 全书(后端展开)

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
            onClick={() => doJob<{ filled: number[]; skipped: number[] }>(
              "回填节拍",
              () => api.refreshBackfillBeats(pid, nums()),
              (r) => toast(`回填完成:${r.filled.length} 章,跳过 ${r.skipped.length} 章`),
            )}>
            回填节拍
          </button>
        </div>

        <div className="card action-card">
          <b>② 初始化文风备忘</b>
          <div className="card-desc">扫前几章正文,生成"这本书怎么写"的文风基准,注入后续生成。已有则不覆盖。</div>
          <button className="btn-sm" disabled={!!busy}
            onClick={() => doJob<{ style_memo: string; seeded: boolean }>(
              "初始化文风备忘",
              () => api.refreshSeedStyleMemo(pid),
              (r) => { setMemo(r.style_memo || ""); toast(r.seeded ? "文风备忘已生成" : "已有文风备忘,未覆盖"); },
            )}>
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
            onClick={() => doJob<{ refreshed: number[]; failed: unknown[]; total: number }>(
              "轻度重润",
              () => api.refreshLight(pid, nums()),
              (r) => toast(`重润完成:${r.refreshed.length}/${r.total} 章`),
            )}>
            轻度重润{picked.size ? `(${picked.size} 章)` : "(全书)"}
          </button>
        </div>

        <div className="card action-card warn-card">
          <b>④ 重度重写</b>
          <div className="card-desc">
            带节拍/概念/文风备忘<b>整章重跑生成</b>,正文会被覆盖(留快照可回滚),并自动重抽圣经、重建下游摘要。
            与逐章生成互斥,按章号顺序串行。
          </div>
          <button className="btn-sm danger" disabled={!!busy}
            onClick={() => {
              if (!confirm("重度重写会覆盖选中章节的正文(有快照可回滚),确定继续?")) return;
              doJob<{ rewritten: number[]; total: number }>(
                "重度重写",
                () => api.refreshHeavy(pid, nums()),
                (r) => toast(`重写完成:${r.rewritten.length}/${r.total} 章`),
              );
            }}>
            重度重写{picked.size ? `(${picked.size} 章)` : "(全书)"}
          </button>
        </div>
      </div>
    </div>
  );
}
