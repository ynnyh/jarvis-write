// 翻新:让已有书按新生成逻辑(节拍/文风备忘/去AI味)重构
// 四件套:回填节拍 → 初始化文风备忘 → 轻度重润(锁情节) / 重度重写(重跑生成)
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ChapterBrief } from "../api";
import { errMsg } from "../pollJob";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";

interface Props { pid: number; }

interface ChapterFailure { chapter: number; error?: string; }
interface HeavyResult {
  rewritten: number[]; total: number; stopped_at: number | null;
  remaining: number[]; error: string | null;
  // 重写后仍撞门禁被拦下(区别于上游报错):stopped_at 即被拦章号,引导去写作页统一处理卡处理
  quarantined?: boolean;
}

export default function RefreshPanel({ pid }: Props) {
  const { run: runJob } = useJob();
  const nav = useNavigate();
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [memo, setMemo] = useState<string>("");
  // 文风备忘手动编辑:editing=编辑态,draft=编辑稿,saving=保存中
  const [memoEditing, setMemoEditing] = useState(false);
  const [memoDraft, setMemoDraft] = useState("");
  const [memoSaving, setMemoSaving] = useState(false);
  // 批量修改要求(可选):跨章的共性问题反馈,轻度重润注入润色 prompt、重度重写作为重写意见
  const [directive, setDirective] = useState("");
  // 失败后的一键补救:轻度重润/回填的失败章、重度重写的剩余章(进度后端已按章保存)
  const [lightFailed, setLightFailed] = useState<number[]>([]);
  const [heavyRemaining, setHeavyRemaining] = useState<number[]>([]);
  // 重度重写命中门禁被拦下的章号(≠上游报错):引导跳写作页用统一处理卡(GateResolve)处理
  const [heavyBlocked, setHeavyBlocked] = useState<number | null>(null);

  function loadChapters() {
    api.listChapters(pid)
      .then((list) => setChapters(list.filter((c) => c.status !== "empty")))
      .catch((e) => setErr(errMsg(e)));
  }
  useEffect(() => { loadChapters(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  // 挂载时拉取现有文风备忘(项目详情),用于查看/编辑;生成文风备忘后也会刷新此值
  useEffect(() => {
    api.getProject(pid)
      .then((p) => setMemo(p.style_memo || ""))
      .catch(() => undefined);
  }, [pid]);

  // 手动保存文风备忘:整段覆盖;清空后生成时会从头自动累积
  async function saveMemo() {
    setMemoSaving(true);
    try {
      const text = memoDraft.trim();
      await api.patchProject(pid, { style_memo: text });
      setMemo(text);
      setMemoEditing(false);
      toast.ok("文风备忘已保存",
        text ? "后续生成会在这份备忘的基础上继续累积" : "已清空,后续生成将重新自动累积");
    } catch (e) {
      toast.err("文风备忘保存失败", errMsg(e));
    } finally {
      setMemoSaving(false);
    }
  }

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
      setErr(errMsg(e));
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
      () => api.refreshLight(pid, list, directive.trim()),
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

  const runHeavy = async (list: number[]) => {
    // 重度重写会覆盖正文:应用内确认框(替代原生 confirm),后果说清、快照兜底也写明
    const scope = list.length ? `选中的 ${list.length} 章` : "全书已成文章节";
    const ok = await confirmDialog({
      title: "重度重写?",
      body: `将整章重跑生成并覆盖${scope}的正文(旧版自动存快照,可回滚),并自动重抽圣经、重建下游摘要。`,
      confirmText: "开始重写",
      danger: true,
    });
    if (!ok) return;
    doJob<HeavyResult>(
      "重度重写",
      () => api.refreshHeavy(pid, list, directive.trim()),
      (r) => {
        setHeavyRemaining(r.remaining || []);
        setHeavyBlocked(r.quarantined ? r.stopped_at : null);
        if (r.quarantined && r.stopped_at)
          // 被门禁拦下(非上游报错):正文已写但与设定有硬矛盾,去写作页用统一处理卡处理
          toast.err(`第 ${r.stopped_at} 章重写后与设定撞了,被拦下`,
            `已完成 ${r.rewritten.length}/${r.total} 章;点下方「去处理」按提示解决,再回来续跑剩余 ${r.remaining.length} 章`);
        else if (r.error)
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

      {/* 批量修改要求:跨章共性问题(文字层)的反馈入口 */}
      <div className="card mb-3">
        <b>修改要求(可选)</b>
        <div className="card-desc mt-1">
          好几章都有的文字层问题写在这里,一次批量处理:比如"每章结尾都在总结点题""对话太书面化"。
          轻度重润会把它注入每章润色 prompt(仍不改剧情);重度重写则作为重写意见。
          单章问题去「写作」用行内重写;剧情/设定层的跨章调整去「大纲」用修改指令。
        </div>
        <textarea className="mt-2" rows={3} maxLength={500}
          placeholder="留空 = 只做常规去AI味重润 / 按原大纲重写"
          value={directive} onChange={(e) => setDirective(e.target.value)} />
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
          <b>② 文风备忘</b>
          <div className="card-desc">
            "这本书怎么写"的文风基准,注入后续生成。可扫前几章自动生成,也可手动查看/修改;
            手改后自动累积会在这份基础上继续。
          </div>
          <button className="btn-sm" disabled={!!busy} onClick={runSeedMemo}>
            生成文风备忘
          </button>
          {!memoEditing ? (
            <>
              {memo ? (
                <details className="mt-2">
                  <summary className="muted">查看文风备忘</summary>
                  <pre className="memo-pre">{memo}</pre>
                </details>
              ) : (
                <div className="hint mt-2">暂无备忘,可自动生成或直接手写。</div>
              )}
              <button className="btn-sm mt-2" disabled={!!busy}
                onClick={() => { setMemoDraft(memo); setMemoEditing(true); }}>
                {memo ? "编辑文风备忘" : "手写文风备忘"}
              </button>
            </>
          ) : (
            <div className="mt-2">
              <textarea rows={8} value={memoDraft}
                placeholder="例:短句为主,对话口语化;章尾不总结点题;比喻节制……"
                onChange={(e) => setMemoDraft(e.target.value)} />
              <div className="mt-2">
                <button className="btn-sm primary" disabled={memoSaving} onClick={saveMemo}>
                  {memoSaving && <span className="spin spin-sm" />}保存备忘
                </button>
                {" "}
                <button className="btn-sm" disabled={memoSaving}
                  onClick={() => setMemoEditing(false)}>取消</button>
              </div>
              <div className="hint mt-1">整段覆盖保存;清空保存 = 后续生成从头重新自动累积。</div>
            </div>
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
            onClick={() => { setHeavyRemaining([]); setHeavyBlocked(null); runHeavy(nums()); }}>
            重度重写{picked.size ? `(${picked.size} 章)` : "(全书)"}
          </button>
          {/* 被门禁拦下:一键跳写作页那一章,用统一处理卡(让 AI 按矛盾重写 / 去改设定 / 忽略继续) */}
          {heavyBlocked !== null && !busy && (
            <button className="btn-sm mt-2"
              title={`第 ${heavyBlocked} 章重写后仍与设定有硬矛盾,未进圣经/摘要。去写作页按提示处理,再回来续跑剩余章`}
              onClick={() => nav(`/project/${pid}/write?ch=${heavyBlocked}`)}>
              去处理第 {heavyBlocked} 章的冲突 →
            </button>
          )}
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
