// 投稿面板:把全书素材压缩成知乎等平台的投稿表单字段(标题/标签/金句/简介/封面提示词)。
// 各项可挑选、微调、一键复制;并提供全书多格式导出,方便去平台发表。
// 生成结果与手动修改都缓存到 localStorage,刷新不丢。
import { useEffect, useState } from "react";
import { AnthemPackage, api, CoverPackage, downloadFile, Project, SubmissionPackage } from "../api";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";

interface Props { pid: number; project: Project; }

// 缓存结构:LLM 产出的候选包 + 用户最终选定/微调的标题与金句
interface SubState { pkg: SubmissionPackage; title: string; hook: string; }

const cacheKey = (pid: number) => `submission-${pid}`;

function loadCache(pid: number): SubState | null {
  try {
    const raw = localStorage.getItem(cacheKey(pid));
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (s && s.pkg) return s as SubState;
  } catch { /* 缓存损坏就当没有 */ }
  return null;
}

// 泛型缓存读取(封面/主题曲卡各自用不同 key):解析失败返回 null
function loadJSON<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch { return null; }
}

// 复制:优先 Clipboard API,不可用时降级到 textarea + execCommand
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch { return false; }
  }
}

function CopyBtn({ text, label = "复制" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  async function go() {
    if (!text.trim()) { toast.err("内容为空", "没有可复制的内容"); return; }
    const ok = await copyText(text);
    if (ok) { setDone(true); setTimeout(() => setDone(false), 1200); }
    else toast.err("复制失败", "请手动选中文本复制");
  }
  return <button className="btn-sm" onClick={go}>{done ? "✓ 已复制" : label}</button>;
}

const SUMMARY_META: { key: "short" | "medium" | "long"; label: string; rows: number }[] = [
  { key: "short", label: "短简介", rows: 3 },
  { key: "medium", label: "中简介(推荐,贴合 50-800 字)", rows: 6 },
  { key: "long", label: "长简介", rows: 9 },
];

export default function SubmissionPanel({ pid, project }: Props) {
  const { run: runJob } = useJob();
  const [state, setState] = useState<SubState | null>(() => loadCache(pid));
  const [busy, setBusy] = useState("");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [newTag, setNewTag] = useState("");

  // 切项目时重新读缓存
  useEffect(() => { setState(loadCache(pid)); }, [pid]);

  function commit(next: SubState) {
    setState(next);
    try { localStorage.setItem(cacheKey(pid), JSON.stringify(next)); } catch { /* 配额满就放弃缓存 */ }
  }
  function patchPkg(patch: Partial<SubmissionPackage>) {
    if (!state) return;
    commit({ ...state, pkg: { ...state.pkg, ...patch } });
  }
  function patchSummary(key: "short" | "medium" | "long", value: string) {
    if (!state) return;
    commit({ ...state, pkg: { ...state.pkg, summaries: { ...state.pkg.summaries, [key]: value } } });
  }

  async function generate() {
    setBusy("AI 正在生成投稿包…"); setErr(""); setStage("");
    try {
      const r = await runJob<SubmissionPackage>(
        () => api.generateSubmissionAsync(pid),
        { kind: `submission-${pid}`, onStage: setStage },
      );
      if (r) {
        commit({ pkg: r, title: r.titles[0] ?? "", hook: r.hooks[0] ?? "" });
        toast.ok("投稿包已生成", "按需挑选/微调后,逐项复制即可");
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); setStage(""); }
  }

  const EXPORTS: { kind: string; label: string; path: string; ext: string }[] = [
    { kind: "txt", label: "纯文本 txt(全章合并)", path: "export/txt", ext: "txt" },
    { kind: "zip", label: "按章分文件 zip", path: "export/chapters-zip", ext: "zip" },
    { kind: "md", label: "Markdown", path: "export/md", ext: "md" },
    { kind: "docx", label: "Word 文档", path: "export/docx", ext: "docx" },
    { kind: "epub", label: "EPUB 电子书", path: "export/epub", ext: "epub" },
  ];
  async function doExport(path: string, ext: string) {
    try {
      await downloadFile(`/api/projects/${pid}/${path}`, `${project.title || pid}.${ext}`);
    } catch (e) { toast.err("导出失败", String(e)); }
  }

  function addTag() {
    if (!state) return;
    const t = newTag.trim();
    if (!t) return;
    if (state.pkg.tags.includes(t)) { toast.info("标签已存在"); setNewTag(""); return; }
    if (state.pkg.tags.length >= 7) { toast.err("最多 7 个标签"); return; }
    patchPkg({ tags: [...state.pkg.tags, t] });
    setNewTag("");
  }
  function removeTag(t: string) {
    if (!state) return;
    patchPkg({ tags: state.pkg.tags.filter((x) => x !== t) });
  }
  async function copyTag(t: string) {
    const ok = await copyText(t);
    if (ok) toast.ok(`已复制标签「${t}」`, "可到平台逐个粘贴");
  }

  const pkg = state?.pkg;
  const title = state?.title ?? "";
  const hook = state?.hook ?? "";

  return (
    <div className="sub-panel">
      {/* 生成 + 导出 */}
      <div className="card">
        <div className="card-head">
          <h3 className="grow">投稿包</h3>
          <button className="primary" disabled={!!busy} onClick={generate}>
            {state ? "重新生成" : "AI 生成投稿包"}
          </button>
        </div>
        <p className="card-desc">
          依据本书的概念、架构与大纲,一次产出标题、频道时空、标签、金句与简介;挑好微调后逐项复制到投稿表单。封面与主题曲提示词在下方单独生成。
        </p>
        {busy && (
          <div className="gen-banner">
            <span className="spin" />
            <span className="gen-banner-text">{stage || busy}</span>
          </div>
        )}
        {err && <div className="msg-err">{err}</div>}
        <div className="actions mt-2">
          <span className="muted">全书导出:</span>
          {EXPORTS.map((e) => (
            <button key={e.kind} className="btn-sm" onClick={() => doExport(e.path, e.ext)}>{e.label}</button>
          ))}
        </div>
      </div>

      {!state && !busy && (
        <div className="card muted">还没生成投稿包。点上方「AI 生成投稿包」,稍等约半分钟即可。</div>
      )}

      {pkg && (
        <>
          {/* 作品名称 ≤15 字 */}
          <div className="card">
            <div className="card-head">
              <h3 className="grow">作品名称</h3>
              <span className={"hint" + (title.length > 15 ? " msg-err" : "")}>{title.length}/15</span>
              <CopyBtn text={title} />
            </div>
            <input type="text" value={title} placeholder="不超过 15 字"
              onChange={(e) => state && commit({ ...state, title: e.target.value })} />
            {pkg.titles.length > 0 && (
              <div className="chips mt-2">
                {pkg.titles.map((t) => (
                  <span key={t} className={"chip" + (t === title ? " on" : "")}
                    onClick={() => state && commit({ ...state, title: t })}>{t}</span>
                ))}
              </div>
            )}
            <p className="hint">点候选名直接选用,也可在输入框里自行修改。</p>
          </div>

          {/* 频道 · 时空 */}
          <div className="card">
            <div className="card-head"><h3 className="grow">频道 · 时空</h3></div>
            <div className="row">
              <div className="sub-kv">
                <span className="muted">频道</span>
                <b>{pkg.channel || "通用"}</b>
                <CopyBtn text={pkg.channel || "通用"} />
              </div>
              <div className="sub-kv">
                <span className="muted">时空</span>
                <input type="text" className="sub-kv-input" value={pkg.era}
                  onChange={(e) => patchPkg({ era: e.target.value })} />
                <CopyBtn text={pkg.era} />
              </div>
            </div>
          </div>

          {/* 作品标签 ≤7 */}
          <div className="card">
            <div className="card-head">
              <h3 className="grow">作品标签</h3>
              <span className={"hint" + (pkg.tags.length > 7 ? " msg-err" : "")}>{pkg.tags.length}/7</span>
              <CopyBtn text={pkg.tags.join(" ")} label="复制全部" />
            </div>
            <div className="chips">
              {pkg.tags.map((t) => (
                <span key={t} className="chip on sub-tag" title="点击复制该标签">
                  <span onClick={() => copyTag(t)}>{t}</span>
                  <i className="sub-tag-x" onClick={() => removeTag(t)}>×</i>
                </span>
              ))}
            </div>
            <div className="input-row mt-2">
              <input type="text" value={newTag} placeholder="自定义标签,回车添加"
                onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }} />
              <button className="btn-sm" onClick={addTag}>添加</button>
            </div>
            <p className="hint">点标签即复制单个;「复制全部」以空格分隔。平台一般逐个录入标签。</p>
          </div>

          {/* 金句描述 ≤25 字 */}
          <div className="card">
            <div className="card-head">
              <h3 className="grow">金句描述</h3>
              <span className={"hint" + (hook.length > 25 ? " msg-err" : "")}>{hook.length}/25</span>
              <CopyBtn text={hook} />
            </div>
            <input type="text" value={hook} placeholder="一句话钩子,不超过 25 字"
              onChange={(e) => state && commit({ ...state, hook: e.target.value })} />
            {pkg.hooks.length > 0 && (
              <div className="chips mt-2">
                {pkg.hooks.map((h) => (
                  <span key={h} className={"chip" + (h === hook ? " on" : "")}
                    onClick={() => state && commit({ ...state, hook: h })}>{h}</span>
                ))}
              </div>
            )}
          </div>

          {/* 作品简介 50-800 字 */}
          <div className="card">
            <div className="card-head"><h3 className="grow">作品简介</h3></div>
            <p className="card-desc">平台要求 50-800 字,推荐用「中简介」。三档都可改、可单独复制。</p>
            {SUMMARY_META.map((m) => {
              const val = pkg.summaries[m.key] ?? "";
              const bad = val.length < 50 || val.length > 800;
              return (
                <div key={m.key} className="sub-summary">
                  <div className="card-head mb-2">
                    <b>{m.label}</b>
                    <span className={"hint" + (bad ? " msg-err" : "")}>
                      {val.length} 字{val.length < 50 ? "(不足 50)" : val.length > 800 ? "(超过 800)" : ""}
                    </span>
                    <CopyBtn text={val} />
                  </div>
                  <textarea rows={m.rows} value={val} onChange={(e) => patchSummary(m.key, e.target.value)} />
                </div>
              );
            })}
          </div>

        </>
      )}

      {/* 封面提示词(独立生成)与主题曲提示词(独立生成) */}
      <CoverCard pid={pid} />
      <AnthemCard pid={pid} />
    </div>
  );
}

// ================= 封面提示词卡(独立生成/缓存) =================
function CoverCard({ pid }: { pid: number }) {
  const { run: runJob } = useJob();
  const [pkg, setPkg] = useState<CoverPackage | null>(() => loadJSON(`cover-${pid}`));
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => { setPkg(loadJSON(`cover-${pid}`)); }, [pid]);

  function commit(next: CoverPackage) {
    setPkg(next);
    try { localStorage.setItem(`cover-${pid}`, JSON.stringify(next)); } catch { /* 配额满就放弃缓存 */ }
  }
  function patch(i: number, field: keyof CoverPackage["covers"][number], value: string) {
    if (!pkg) return;
    commit({ covers: pkg.covers.map((c, j) => (j === i ? { ...c, [field]: value } : c)) });
  }

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await runJob<CoverPackage>(
        () => api.generateCoverAsync(pid),
        { kind: `cover-${pid}`, onStage: setStage },
      );
      if (r) { commit(r); toast.ok("封面提示词已生成", "复制到即梦 / Midjourney 生成封面即可"); }
    } catch (e) { setErr(String(e)); } finally { setBusy(false); setStage(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">封面提示词</h3>
        <button className="primary" disabled={busy} onClick={generate}>
          {pkg ? "重新生成" : "AI 生成封面提示词"}
        </button>
      </div>
      <p className="card-desc">
        依据本书素材出 3 套风格各异的封面画面提示词(中文版给即梦、英文版给 Midjourney,附负面词);复制拿去自己生成封面图。
      </p>
      {busy && (
        <div className="gen-banner"><span className="spin" /><span className="gen-banner-text">{stage || "AI 正在设计封面…"}</span></div>
      )}
      {err && <div className="msg-err">{err}</div>}
      {!pkg && !busy && <p className="hint">还没生成。点上方按钮,约半分钟出 3 套方案。</p>}
      {pkg?.covers.map((c, i) => (
        <div key={i} className="sub-summary">
          <div className="card-head mb-2">
            <b>方案 {i + 1}{c.style ? `· ${c.style}` : ""}</b>
          </div>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">中文提示词(即梦等)</span><CopyBtn text={c.prompt_cn} /></div>
            <textarea rows={4} value={c.prompt_cn} onChange={(e) => patch(i, "prompt_cn", e.target.value)} />
          </div>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">英文提示词(Midjourney)</span><CopyBtn text={c.prompt_en} /></div>
            <textarea rows={3} value={c.prompt_en} onChange={(e) => patch(i, "prompt_en", e.target.value)} />
          </div>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">负面提示词</span><CopyBtn text={c.negative} /></div>
            <textarea rows={2} value={c.negative} onChange={(e) => patch(i, "negative", e.target.value)} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ================= 主题曲提示词卡(Suno,独立生成/缓存) =================
function AnthemCard({ pid }: { pid: number }) {
  const { run: runJob } = useJob();
  const [pkg, setPkg] = useState<AnthemPackage | null>(() => loadJSON(`anthem-${pid}`));
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => { setPkg(loadJSON(`anthem-${pid}`)); }, [pid]);

  function commit(next: AnthemPackage) {
    setPkg(next);
    try { localStorage.setItem(`anthem-${pid}`, JSON.stringify(next)); } catch { /* 配额满就放弃缓存 */ }
  }
  function patch(field: keyof AnthemPackage, value: string) {
    if (!pkg) return;
    commit({ ...pkg, [field]: value });
  }

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await runJob<AnthemPackage>(
        () => api.generateAnthemAsync(pid),
        { kind: `anthem-${pid}`, onStage: setStage },
      );
      if (r) { commit(r); toast.ok("主题曲已生成", "把风格标签和歌词分别粘进 Suno 即可"); }
    } catch (e) { setErr(String(e)); } finally { setBusy(false); setStage(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">主题曲提示词(Suno)</h3>
        <button className="primary" disabled={busy} onClick={generate}>
          {pkg ? "重新生成" : "AI 生成主题曲"}
        </button>
      </div>
      <p className="card-desc">
        为本书量身写一首主题曲:英文风格标签 + 结构化中文歌词。到 Suno 把「风格标签」填 Style、「歌词」填 Lyrics 即可生成。
      </p>
      {busy && (
        <div className="gen-banner"><span className="spin" /><span className="gen-banner-text">{stage || "AI 正在作词谱曲…"}</span></div>
      )}
      {err && <div className="msg-err">{err}</div>}
      {!pkg && !busy && <p className="hint">还没生成。点上方按钮,约半分钟出一首。</p>}
      {pkg && (
        <>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">歌名</span><CopyBtn text={pkg.song_title} /></div>
            <input type="text" value={pkg.song_title} onChange={(e) => patch("song_title", e.target.value)} />
          </div>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">风格标签(填进 Suno 的 Style of Music)</span><CopyBtn text={pkg.style_tags} /></div>
            <textarea rows={2} value={pkg.style_tags} onChange={(e) => patch("style_tags", e.target.value)} />
          </div>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">歌词(填进 Suno 的 Lyrics)</span><CopyBtn text={pkg.lyrics} /></div>
            <textarea rows={12} value={pkg.lyrics} onChange={(e) => patch("lyrics", e.target.value)} />
          </div>
          {pkg.vibe && <p className="hint">{pkg.vibe}</p>}
        </>
      )}
    </div>
  );
}
