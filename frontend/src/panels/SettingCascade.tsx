// 设定级级联流程卡:作者改了世界观硬规则 → 扫描全书受影响章节(章级粗筛 +
// 段级定位,后端用逐字引文反查段号防幻觉)→ 勾选冲突段生成定点修提案 →
// 复用 AnnotatedReviseCard 逐条 diff 验收(接受走 paraEdit 快照守卫写回)。
// 验收过修改的章自动触发重抽取(同步圣经/摘要),防止改完正文圣经脱账。
// 挂载即开扫(父级在用户确认后才挂载本组件);扫描结果由后端回显权威 diff,
// 生成提案时原样传回,前端不做二次 diff。
import { useEffect, useRef, useState } from "react";
import {
  api, ChapterDetail, SettingChange, SettingPatchResult, SettingScanResult,
} from "../api";
import { emitChapterSaved } from "../desktop";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";
import { useJob } from "../ui/useJob";
import AnnotatedReviseCard from "./write/AnnotatedReviseCard";

interface Props {
  pid: number;
  oldText: string;
  newText: string;
  onClose: () => void;
}

export default function SettingCascade({ pid, oldText, newText, onClose }: Props) {
  const { run } = useJob();
  const [scan, setScan] = useState<SettingScanResult | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set()); // `${chapter}:${para_idx}`
  const [patch, setPatch] = useState<SettingPatchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // 生成提案要用的变更清单(scan 时后端 diff 好的),存 ref 不参与渲染
  const changesRef = useRef<SettingChange[]>([]);
  // StrictMode 双挂载防重:同一进程只发一次扫描
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    setBusy(true);
    run<SettingScanResult>(
      () => api.settingCascadeScan(pid, oldText, newText),
      { kind: `setting-scan-${pid}` },
    ).then((r) => {
      if (!r) return; // 本地等待被中止(任务中心仍可见)
      changesRef.current = r.changes;
      setScan(r);
      setPicked(new Set(r.passages.map((p) => `${p.chapter_number}:${p.para_idx}`)));
    }).catch((e) => setErr(errMsg(e)))
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 生成定点修提案(只对勾选段;提案不落库,进入逐条验收)
  async function makePatches() {
    if (!scan || !picked.size) return;
    setBusy(true); setErr("");
    try {
      const passages = scan.passages
        .filter((p) => picked.has(`${p.chapter_number}:${p.para_idx}`))
        .map((p) => ({ chapter_number: p.chapter_number, para_idx: p.para_idx }));
      const r = await run<SettingPatchResult>(
        () => api.settingCascadePatch(pid, changesRef.current, passages),
        { kind: `setting-patch-${pid}` },
      );
      if (r) setPatch(r);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  }

  // 单章验收卡收起:该章有被接受的修改 → 自动触发重抽取(每章一次),同步圣经/摘要
  function onChapterClosed(chapterNumber: number, hadAccepted: boolean) {
    if (hadAccepted) {
      api.reExtractAsync(pid, chapterNumber)
        .then(() => toast.ok(`第 ${chapterNumber} 章已更新`, "正在后台重抽设定事实与摘要,同步故事圣经"))
        .catch(() => toast.err("重抽取未启动", `第 ${chapterNumber} 章正文已改好,可稍后在审核报告手动重抽`));
      void emitChapterSaved(pid, chapterNumber);
    }
    if (!patch) return;
    const rest = patch.chapters.filter((c) => c.chapter_number !== chapterNumber);
    setPatch(rest.length ? { ...patch, chapters: rest } : null);
    if (!rest.length) onClose();
  }

  const scanning = busy && !scan;
  const done = scan && !busy;

  return (
    <div className="card card-compact">
      <div className="card-head">
        <h3 className="grow">设定级级联</h3>
        {!busy && <button className="btn-sm" onClick={onClose}>关闭</button>}
      </div>

      {scanning && (
        <div className="muted"><span className="spin spin-sm" /> 正在逐章扫描影响(先粗筛、再对命中章定位冲突段落)…</div>
      )}
      {err && <div className="msg-err mt-1">{err}</div>}

      {done && scan && (
        <>
          <div className="hint mt-1">
            共扫描 {scan.screened} 章:命中 <b>{scan.affected_chapters.length}</b> 章、
            定位 <b>{scan.passages.length}</b> 处冲突段
            {scan.unlocated > 0 && <>(另有 {scan.unlocated} 处引文对不上正文,已丢弃)</>}
            {scan.failed.length > 0 && <>;<b>{scan.failed.length} 章扫描失败</b>(可关闭后重试)</>}。
          </div>
          {scan.affected_chapters.length > 0 && (
            <div className="mt-1">
              <label className="fl">受影响章节</label>
              {scan.affected_chapters.map((c) => (
                <div key={c.chapter_number} className="hint">
                  · 第 {c.chapter_number} 章{c.title ? `《${c.title}》` : ""}:{c.reason}
                </div>
              ))}
            </div>
          )}
          {!patch && scan.passages.length > 0 && (
            <>
              <label className="fl mt-2">冲突段落(勾选要生成修订提案的段落)</label>
              <div className="chips">
                {scan.passages.map((p) => {
                  const key = `${p.chapter_number}:${p.para_idx}`;
                  const on = picked.has(key);
                  return (
                    <button key={key} type="button" className={"chip" + (on ? " on" : "")}
                      onClick={() => setPicked((s) => {
                        const n = new Set(s);
                        if (n.has(key)) n.delete(key); else n.add(key);
                        return n;
                      })}>
                      第{p.chapter_number}章·第{p.para_idx + 1}段
                    </button>
                  );
                })}
              </div>
              <div className="mt-1">
                {scan.passages.filter((p) => picked.has(`${p.chapter_number}:${p.para_idx}`)).map((p) => (
                  <div key={`${p.chapter_number}:${p.para_idx}`} className="hint">
                    · 第 {p.chapter_number} 章第 {p.para_idx + 1} 段「{p.quote}」:{p.reason}
                  </div>
                ))}
              </div>
              <div className="actions mt-2">
                <button className="primary btn-sm" disabled={!picked.size || busy}
                  onClick={() => void makePatches()}>
                  生成修订提案({picked.size})
                </button>
              </div>
            </>
          )}
          {!patch && scan.affected_chapters.length === 0 && scan.passages.length === 0 && (
            <div className="hint mt-1">没有章节受这次设定变更影响,放心使用。</div>
          )}
        </>
      )}

      {patch && patch.chapters.map((g) => (
        <SettingPatchChapterCard
          key={g.chapter_number} pid={pid} chapterNumber={g.chapter_number} pairs={g.pairs}
          onChapterClosed={onChapterClosed} />
      ))}
    </div>
  );
}

// 单章验收:拉章详情 → 复用 AnnotatedReviseCard(与全书批修同一交互);
// 记录该章是否接受过修改,收起时交给父级触发重抽取。
function SettingPatchChapterCard({ pid, chapterNumber, pairs, onChapterClosed }: {
  pid: number; chapterNumber: number;
  pairs: SettingPatchResult["chapters"][number]["pairs"];
  onChapterClosed: (chapterNumber: number, hadAccepted: boolean) => void;
}) {
  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [err, setErr] = useState("");
  const hadAcceptedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api.getChapter(pid, chapterNumber)
      .then((c) => { if (!cancelled) setChapter(c); })
      .catch((e) => { if (!cancelled) setErr(String(e)); });
    return () => { cancelled = true; };
  }, [pid, chapterNumber]);

  if (err) {
    return <div className="card mt-2"><div className="msg-err">第 {chapterNumber} 章详情加载失败:{err}</div></div>;
  }
  if (!chapter) {
    return <div className="card muted mt-2"><span className="spin spin-sm" /> 正在载入第 {chapterNumber} 章正文…</div>;
  }
  return (
    <div className="mt-2">
      <AnnotatedReviseCard
        pid={pid} chapter={chapter} pairs={pairs} heading="设定定点修"
        onSaved={(updated) => setChapter(updated)}
        onPairAccepted={() => { hadAcceptedRef.current = true; }}
        onClose={() => onChapterClosed(chapterNumber, hadAcceptedRef.current)} />
    </div>
  );
}
