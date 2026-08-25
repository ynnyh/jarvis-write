// 情绪短片工坊:15/30 秒命题短视频,一次产三个本子三选一(快节奏,不磨单版)。
// 双入口共用:书房「情绪短片」(通用命题)与小说项目「投流」页签(小说衍生,金句溯源)。
// 交互三层细化:①建卡可指定导向维度(台词风格/节奏/情绪浓度/氛围关键词);
// ②候选卡可展开看分镜台词再选,单条可带意见重拍,换一批可带反馈;
// ③选定后的手卡可手动编辑(改台词/分镜/提示词)保存。
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ClipCard, ClipDirection, ClipShot, ClipTheme, MoodClip, SteeringOption, clipsApi } from "../clipsApi";
import { api } from "../api";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";
import { errMsg, pollJob } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import Banner from "../ui/Banner";
import { CopyBtn } from "../ui/copy";
import { confirmDialog } from "../ui/ConfirmDialog";

interface ClipsMeta {
  themes: ClipTheme[];
  durations: number[];
  directions: ClipDirection[];
  dialogue_styles: SteeringOption[];
  pacings: SteeringOption[];
  intensities: SteeringOption[];
}

function useClipsMeta() {
  const [meta, setMeta] = useState<ClipsMeta | null>(null);
  useEffect(() => { void clipsApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

export default function ClipsPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const projectId = params.get("project");
  return id ? <ClipWorkspace cid={Number(id)} /> : <ClipsList projectId={projectId ? Number(projectId) : null} />;
}

/** 导向维度 chips 组(auto=AI 定;目录由后端下发,前端只渲染) */
function SteeringChips({ label, options, value, onChange }: {
  label: string; options: SteeringOption[]; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <span className="fl">{label}<span className="hint">可选</span></span>
      <div className="chips">
        {options.map((o) => (
          <button key={o.key} type="button"
            className={"chip" + (value === o.key ? " on" : "")}
            aria-pressed={value === o.key}
            onClick={() => onChange(o.key)}>{o.label}</button>
        ))}
      </div>
    </div>
  );
}

// ================= 列表 + 新建(通用/小说衍生共用) =================
export function ClipsList({ projectId }: { projectId: number | null }) {
  const nav = useNavigate();
  const meta = useClipsMeta();
  const [rows, setRows] = useState<MoodClip[] | null>(null);
  const [theme, setTheme] = useState("");
  const [custom, setCustom] = useState("");
  const [duration, setDuration] = useState(15);
  const [direction, setDirection] = useState("live");
  const [inspiration, setInspiration] = useState("");
  const [dialogueStyle, setDialogueStyle] = useState("auto");
  const [pacing, setPacing] = useState("auto");
  const [intensity, setIntensity] = useState("auto");
  const [styleHints, setStyleHints] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { setRows((await clipsApi.list(projectId ?? undefined)).clips); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, [projectId]);
  useEffect(() => { void reload(); }, [reload]);

  async function create() {
    if (!theme && !custom.trim()) { toast.err("先选主题", "选一个情绪主题,或填自定义"); return; }
    setBusy(true);
    try {
      const r = await clipsApi.create({
        theme: theme || undefined, custom_theme: custom.trim(), duration_s: duration,
        direction, inspiration: inspiration.trim(),
        source_project_id: projectId ?? undefined,
        dialogue_style: dialogueStyle, pacing, intensity,
        style_hints: styleHints.trim(),
      });
      toast.ok("已建,马上开产三个本子", "进工作台看进度;不合适可带意见换一批");
      // autostart:工作台 mount 时自动触发生成,补上"建完还要再点一次生成"的断档
      nav(`/clips/${r.clip_row.id}`, { state: { autostart: true } });
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setBusy(false); }
  }

  const novelMode = projectId !== null;
  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);

  return (
    <>
      {!novelMode && (
        <div className="page-head">
          <h1>情绪短片</h1>
          <button className="btn" onClick={() => nav("/")}>← 我的小说</button>
        </div>
      )}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            {novelMode ? "为这本书出投流短视频" : "新建情绪短片"}
            <span className="muted">{novelMode ? "从书里抽金句名场面,金句可溯源" : "15/30 秒,一次三本子三选一"}</span>
          </h3>
        </div>
        <p className="card-desc">
          {novelMode
            ? "AI 读你的定稿章节,挑最戳人的金句与名场面,一次产 3 个不同切入的投流本子——核心金句必须出自正文,引擎会逐句溯源校验。"
            : "选个情绪命题(遗憾/争吵/爱情/童趣…),AI 一次给 3 个不同切入的本子:钩子开场 → 情绪蓄势 → 金句收尾,每格带三轨提示词与切段,拿去即梦/剪映直接出片。"}
        </p>
        <div className="form-grid">
          {!novelMode && (
            <div className="field field-full">
              <span className="fl">情绪命题<span className="hint">选一个,或用自定义</span></span>
              <div className="chips">
                {(meta?.themes ?? []).map((t) => (
                  <button key={t.key} type="button"
                    className={"chip" + (theme === t.key ? " on" : "")}
                    aria-pressed={theme === t.key}
                    onClick={() => { setTheme(t.key); setCustom(""); }}>{t.label}</button>
                ))}
                <button type="button"
                  className={"chip custom" + (!theme && custom ? " on" : "")}
                  aria-pressed={!theme && !!custom}
                  onClick={() => setTheme("")}>自定义</button>
              </div>
            </div>
          )}
          {!theme && !novelMode && (
            <div className="field field-full">
              <label className="fl" htmlFor="clip-custom">自定义主题</label>
              <input id="clip-custom" value={custom} maxLength={40} placeholder="如「毕业前夜」"
                onChange={(e) => setCustom(e.target.value)} />
            </div>
          )}
          {novelMode && (
            <div className="field">
              <label className="fl" htmlFor="clip-mood">
                情绪侧重<span className="hint">可选,影响选材倾向</span>
              </label>
              <select id="clip-mood" value={theme} onChange={(e) => setTheme(e.target.value)}>
                <option value="">AI 按书自动挑</option>
                {(meta?.themes ?? []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
            </div>
          )}
          <div className="field">
            <label className="fl" htmlFor="clip-duration">时长</label>
            <select id="clip-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              <option value={15}>15 秒</option>
              <option value={30}>30 秒</option>
            </select>
          </div>
          <div className="field field-full">
            <span className="fl">画风<span className="hint">决定整套风格卡,候选共用</span></span>
            <div className="chips">
              {(meta?.directions ?? []).map((d) => (
                <button key={d.key} type="button"
                  className={"chip" + (direction === d.key ? " on" : "")}
                  aria-pressed={direction === d.key}
                  onClick={() => setDirection(d.key)}>{d.label}</button>
              ))}
            </div>
            {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
          </div>
          <SteeringChips label="台词风格" options={meta?.dialogue_styles ?? [{ key: "auto", label: "AI 定" }]}
            value={dialogueStyle} onChange={setDialogueStyle} />
          <SteeringChips label="节奏" options={meta?.pacings ?? [{ key: "auto", label: "AI 定" }]}
            value={pacing} onChange={setPacing} />
          <SteeringChips label="情绪浓度" options={meta?.intensities ?? [{ key: "auto", label: "AI 定" }]}
            value={intensity} onChange={setIntensity} />
          <div className="field field-full">
            <label className="fl" htmlFor="clip-hints">
              氛围关键词<span className="hint">可选,并进画风卡;如「雨夜便利店、暖光、旧磁带」</span>
            </label>
            <input id="clip-hints" value={styleHints} maxLength={80}
              onChange={(e) => setStyleHints(e.target.value)}
              placeholder="不填则 AI 按命题自定氛围" />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="clip-inspire">
              一句话灵感<span className="hint">可选,故事种子</span>
            </label>
            <input id="clip-inspire" value={inspiration} maxLength={60}
              onChange={(e) => setInspiration(e.target.value)}
              placeholder={novelMode ? "如「主打男女主第一次对峙」(不填则 AI 自动挑)" : "如「异地恋的最后一通电话」(不填则 AI 自由发挥)"} />
          </div>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={busy} onClick={create}>
            {busy ? "正在建…" : "产 3 个本子(三选一)"}
          </button>
          <span className="form-actions-tip">三个本子切入各不相同;不合适可带意见整批换,或单条重拍。</span>
        </div>
      </section>

      {rows === null ? <p className="muted">加载中…</p> : rows.length === 0 ? (
        <EmptyState>{novelMode ? "这本书还没有投流短视频——上面建一个。" : "还没有短片。选个主题试试,三十秒出三个本子。"}</EmptyState>
      ) : rows.map((r) => (
        <div key={r.id} className="sub-summary ep-row"
          onClick={() => nav(`/clips/${r.id}`)}>
          <div className="card-head mb-2">
            <b>{(r.clip as ClipCard).take ? `《${(r.clip as ClipCard).take}》` : ""}{r.theme_display}{r.custom_theme && !r.theme ? `·${r.custom_theme}` : ""}</b>
            <span className="badge">{r.duration_s}s</span>
            <span className="badge">{r.direction_label}</span>
            <span className="badge">{r.status_cn}</span>
            <span className="grow" />
            <button className="btn-sm" onClick={(e) => {
              e.stopPropagation();
              void (async () => {
                const ok = await confirmDialog({
                  title: "删除这条短片企划?",
                  body: "三个本子与已选定的手卡都会一起删掉,不可恢复。",
                  confirmText: "确认删除",
                  danger: true,
                });
                if (!ok) return;
                try { await clipsApi.remove(r.id); await reload(); }
                catch (err) { toast.err("删除失败", errMsg(err)); }
              })();
            }}>删除</button>
          </div>
          {(r.clip as ClipCard).logline && <div className="muted">{(r.clip as ClipCard).logline}</div>}
        </div>
      ))}
    </>
  );
}

// ================= 单条工作台:批产 → 三选一 → 手卡 =================

/** 生成参数编辑(建后可调):改动保存后对下一次「换一批/重拍」生效 */
function ClipParamsEditor({ row, onSaved }: { row: MoodClip; onSaved: (r: MoodClip) => void }) {
  const meta = useClipsMeta();
  const [duration, setDuration] = useState(row.duration_s);
  const [direction, setDirection] = useState(row.direction);
  const [inspiration, setInspiration] = useState(row.inspiration);
  const [dialogueStyle, setDialogueStyle] = useState(row.dialogue_style || "auto");
  const [pacing, setPacing] = useState(row.pacing || "auto");
  const [intensity, setIntensity] = useState(row.intensity || "auto");
  const [styleHints, setStyleHints] = useState(row.style_hints || "");
  const [busy, setBusy] = useState(false);
  const dirty = duration !== row.duration_s || direction !== row.direction
    || inspiration !== row.inspiration || dialogueStyle !== (row.dialogue_style || "auto")
    || pacing !== (row.pacing || "auto") || intensity !== (row.intensity || "auto")
    || styleHints !== (row.style_hints || "");

  async function save() {
    setBusy(true);
    try {
      const r = await clipsApi.patch(row.id, {
        duration_s: duration, direction,
        inspiration: inspiration.trim(),
        dialogue_style: dialogueStyle, pacing, intensity,
        style_hints: styleHints.trim(),
      });
      onSaved(r.clip_row);
      toast.ok("参数已保存", "对下一次「换一批/重拍」生效");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setBusy(false); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);

  return (
    <div className="form-grid">
      <div className="field">
        <label className="fl" htmlFor="cp-duration">时长</label>
        <select id="cp-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
          <option value={15}>15 秒</option>
          <option value={30}>30 秒</option>
        </select>
      </div>
      <div className="field">
        <label className="fl" htmlFor="cp-direction">画风</label>
        <select id="cp-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
          {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
      </div>
      <SteeringChips label="台词风格" options={meta?.dialogue_styles ?? [{ key: "auto", label: "AI 定" }]}
        value={dialogueStyle} onChange={setDialogueStyle} />
      <SteeringChips label="节奏" options={meta?.pacings ?? [{ key: "auto", label: "AI 定" }]}
        value={pacing} onChange={setPacing} />
      <SteeringChips label="情绪浓度" options={meta?.intensities ?? [{ key: "auto", label: "AI 定" }]}
        value={intensity} onChange={setIntensity} />
      <div className="field">
        <label className="fl" htmlFor="cp-hints">氛围关键词</label>
        <input id="cp-hints" value={styleHints} maxLength={80}
          onChange={(e) => setStyleHints(e.target.value)} placeholder="如「雨夜便利店、暖光」" />
      </div>
      <div className="field field-full">
        <label className="fl" htmlFor="cp-inspire">一句话灵感</label>
        <input id="cp-inspire" value={inspiration} maxLength={60}
          onChange={(e) => setInspiration(e.target.value)} placeholder="故事种子,可空" />
      </div>
      <div className="form-actions">
        <button className="primary btn-sm" disabled={busy || !dirty} onClick={() => void save()}>保存参数</button>
        {!dirty && <span className="form-actions-tip">与当前一致</span>}
      </div>
    </div>
  );
}

function ClipWorkspace({ cid }: { cid: number }) {
  const nav = useNavigate();
  const location = useLocation() as { state?: { autostart?: boolean } };
  const { run } = useJob();
  const [row, setRow] = useState<MoodClip | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  // 换一批的用户意见(可选):连同上一批切入进提示词,这批避开旧方向
  const [feedback, setFeedback] = useState("");
  // 展开分镜预览的候选序号(-1 收起):选定前就能看到真东西,不盲选
  const [expanded, setExpanded] = useState(-1);
  // 单条重拍的序号与其意见输入
  const [rerollIdx, setRerollIdx] = useState(-1);
  const [rerollFeedback, setRerollFeedback] = useState("");
  const bootedRef = useRef(false);

  const reload = useCallback(async () => {
    try { setRow((await clipsApi.get(cid)).clip_row); }
    catch (e) { setErr(errMsg(e)); }
  }, [cid]);
  useEffect(() => { void reload(); }, [reload]);

  const attach = useCallback(async (jobId: string) => {
    // 挂到一个已在跑的任务(恢复场景):轮询到完成/失败,进度喂横幅
    setBusy(true); setErr(""); setStage("");
    try {
      await pollJob(jobId, { onStage: setStage });
      await reload();
      toast.ok("完成", "内容已更新");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }, [reload]);

  useEffect(() => {
    // 等 row 加载完成才启动一次:row 没回来时 status 判不了,autostart 会漏发
    if (bootedRef.current || row === null) return;
    bootedRef.current = true;
    void (async () => {
      let hit: { job_id: string; kind: string } | undefined;
      try {
        // 先看有没有跑着的任务(切页回来恢复);再处理建卡后的自动开跑
        const { jobs } = await api.myJobs();
        hit = jobs.find((j) => j.kind === `clips-gen-${cid}` || j.kind === `clips-reexp-${cid}`);
      } catch { /* 任务列表拉不到就静默,不挡页面 */ }
      if (hit) { await attach(hit.job_id); return; }
      if (location.state?.autostart && row.status === "draft") {
        try {
          const r = await clipsApi.generate(cid);
          await attach(r.job_id);
        } catch (e) { setErr(errMsg(e)); }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row, cid]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      await run(() => clipsApi.generate(cid, feedback.trim() || undefined),
        { kind: `clips-gen-${cid}`, onStage: setStage });
      await reload();
      toast.ok("三个本子已出", "点开卡片看分镜再选;不合适带意见换一批");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function reroll(index: number) {
    setBusy(true); setErr(""); setStage("");
    try {
      await run(() => clipsApi.reexpand(cid, index, rerollFeedback.trim() || undefined),
        { kind: `clips-reexp-${cid}`, onStage: setStage });
      await reload();
      toast.ok("重拍完成", "切入与画风不变,分镜已换");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); setRerollIdx(-1); setRerollFeedback(""); }
  }

  async function pick(index: number) {
    try {
      const r = await clipsApi.pick(cid, index);
      setRow(r.clip_row);
      toast.ok("已选定", "手卡下方可导出,细节不顺眼可直接编辑");
    } catch (e) { toast.err("选择失败", errMsg(e)); }
  }

  async function exp(fmt: "md" | "srt" | "json") {
    try { await clipsApi.export(cid, fmt); } catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  async function saveCard(card: ClipCard) {
    const r = await clipsApi.saveCard(cid, card);
    setRow(r.clip_row);
  }

  if (row === null && !err) return <p className="muted">加载中…</p>;
  if (row === null) return <div className="msg-err">{err}</div>;

  const candidates = row.candidates ?? [];
  const chosen = row.chosen >= 0 ? (row.clip as ClipCard) : null;

  return (
    <>
      <div className="page-head">
        <h1>
          {row.theme_display}{row.custom_theme && !row.theme ? `·${row.custom_theme}` : ""}
          <span className="badge">{row.duration_s}s</span>
          <span className="badge">{row.direction_label}</span>
          <span className="badge">{row.status_cn}</span>
        </h1>
        <button className="btn" onClick={() => nav(row.source_project_id ? `/project/${row.source_project_id}/book?tab=clips` : "/clips")}>← 返回</button>
      </div>

      {!busy && (
        <details className="clip-params">
          <summary className="fld-hint" style={{ cursor: "pointer" }}>
            生成参数(时长/画风/导向——改完保存,对下一次生成生效)
          </summary>
          <div style={{ marginTop: 8 }}>
            <ClipParamsEditor row={row} onSaved={setRow} />
          </div>
        </details>
      )}

      <section className="card">
        <div className="card-head">
          <h3 className="grow">{candidates.length ? "三个本子,点卡选定" : "生成"}</h3>
          <button className="primary" disabled={busy} onClick={generate}>
            {candidates.length ? "换一批" : "产 3 个本子"}
          </button>
        </div>
        {candidates.length > 0 && (
          <div className="field">
            <label className="fl" htmlFor="clip-feedback">
              换一批的意见<span className="hint">可选:这批哪里不满意,下一批照着调</span>
            </label>
            <input id="clip-feedback" value={feedback} maxLength={200}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="如「金句太鸡汤,要更扎心的;切入别再写父女」" />
          </div>
        )}
        {busy && <Banner stage={stage} text="AI 正在产本子…" />}
        {err && <div className="msg-err">{err}</div>}
        {candidates.map((c, i) => (
          <div key={i} className={"sub-summary ep-row" + (row.chosen === i ? " ep-on" : "")}>
            <div className="card-head mb-2">
              <b>本子 {i + 1} · {c.take}</b>
              {c.hook_text && <span className="badge">钩子:{c.hook_text}</span>}
              <span className="grow" />
              <button className="btn-sm" disabled={busy}
                onClick={() => setExpanded(expanded === i ? -1 : i)}>
                {expanded === i ? "收起分镜" : "看分镜"}
              </button>
              <button className={"btn-sm" + (row.chosen === i ? " primary" : "")}
                onClick={() => void pick(i)}>{row.chosen === i ? "✓ 已选定" : "选定这个"}</button>
            </div>
            <div>{c.logline}</div>
            {c.emotion_curve && <div className="muted">情绪曲线:{c.emotion_curve}</div>}
            {c.punchline && <div><b>金句:</b>{c.punchline}</div>}
            {c.quote_source && <div className="muted">原句:{c.quote_source}</div>}
            {c.cautions?.length > 0 && <div className="warn-tip">⚠ {c.cautions.join(";")}</div>}
            <div className="muted">
              {c.shots.length} 格 · {c.shots.reduce((s, x) => s + x.duration_s, 0)}s · 切 {c.chunks.length} 段
            </div>
            {expanded === i && (
              <div className="clip-preview">
                {c.lines?.length > 0 && (
                  <div className="sub-summary">
                    {c.lines.map((l, j) => (
                      <div key={j} className="script-line"><b>{l.speaker}</b>:{l.text}
                        {l.action && <span className="muted">(画面:{l.action})</span>}
                      </div>
                    ))}
                  </div>
                )}
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead><tr><th>#</th><th>场景</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr></thead>
                    <tbody>
                      {c.shots.map((s) => (
                        <tr key={s.seq}>
                          <td>{s.seq}</td><td>{s.scene_name}</td><td>{s.shot_type}</td>
                          <td>{s.camera}</td><td>{s.duration_s}</td>
                          <td>{s.action_desc}</td><td>{s.dialogue}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {rerollIdx === i ? (
                  <div className="field">
                    <label className="fl" htmlFor={`reroll-${i}`}>
                      重拍意见<span className="hint">可选:只重展开分镜,切入与画风不变</span>
                    </label>
                    <input id={`reroll-${i}`} value={rerollFeedback} maxLength={200}
                      onChange={(e) => setRerollFeedback(e.target.value)}
                      placeholder="如「台词砍半,前两格合并,特写留给最后」" />
                    <div className="form-actions">
                      <button className="primary btn-sm" disabled={busy} onClick={() => void reroll(i)}>开拍</button>
                      <button className="btn-sm" onClick={() => { setRerollIdx(-1); setRerollFeedback(""); }}>取消</button>
                    </div>
                  </div>
                ) : (
                  <button className="btn-sm" disabled={busy} onClick={() => setRerollIdx(i)}>↻ 重拍这条(分镜不行时)</button>
                )}
              </div>
            )}
          </div>
        ))}
      </section>

      {chosen && (
        <ClipHandcard card={chosen} onExport={exp} onSave={saveCard} />
      )}
    </>
  );
}

/** 手卡:选定本子的完整物料(分镜/三轨/金句/切段),可编辑保存;通用页与小说面板共用 */
export function ClipHandcard({ card, onExport, onSave }: {
  card: ClipCard;
  onExport: (fmt: "md" | "srt" | "json") => void;
  onSave?: (card: ClipCard) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ClipCard | null>(null);
  const [busy, setBusy] = useState(false);

  function startEdit() {
    // 深拷贝:编辑期间原卡不动,取消可无损还原
    setDraft(JSON.parse(JSON.stringify(card)) as ClipCard);
    setEditing(true);
  }

  async function save() {
    if (!draft) return;
    if (!onSave) return;
    setBusy(true);
    try {
      await onSave(draft);
      setEditing(false); setDraft(null);
      toast.ok("手卡已保存", "切段与总时长已按新分镜重算");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setBusy(false); }
  }

  const view = editing && draft ? draft : card;
  const upd = (patch: Partial<ClipCard>) => setDraft({ ...(draft as ClipCard), ...patch });
  const updShot = (seq: number, patch: Partial<ClipShot>) => setDraft({
    ...(draft as ClipCard),
    shots: (draft as ClipCard).shots.map((s) => (s.seq === seq ? { ...s, ...patch } : s)),
  });

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">手卡 · 《{view.take}》{editing ? "(编辑中)" : ""}</h3>
        {!editing && <CopyBtn text={card.punchline} label="复制金句" />}
        {!editing && <button className="btn-sm" onClick={() => onExport("md")}>导出手卡</button>}
        {!editing && <button className="btn-sm" onClick={() => onExport("srt")}>字幕SRT</button>}
        {!editing && <button className="btn-sm" onClick={() => onExport("json")}>JSON</button>}
        {onSave && !editing && <button className="btn-sm" onClick={startEdit}>✍ 编辑</button>}
        {editing && <button className="primary btn-sm" disabled={busy} onClick={() => void save()}>保存</button>}
        {editing && <button className="btn-sm" disabled={busy} onClick={() => { setEditing(false); setDraft(null); }}>取消</button>}
      </div>
      {editing ? (
        <div className="form-grid">
          <div className="field">
            <label className="fl">金句(结尾字幕卡)</label>
            <input value={view.punchline} maxLength={60} onChange={(e) => upd({ punchline: e.target.value })} />
          </div>
          <div className="field">
            <label className="fl">钩子文案</label>
            <input value={view.hook_text} maxLength={60} onChange={(e) => upd({ hook_text: e.target.value })} />
          </div>
          <div className="field field-full">
            <label className="fl">台词(每句一行;留空即无台词)</label>
            {(view.lines ?? []).map((l, i) => (
              <div key={i} className="clip-edit-line">
                <input className="clip-edit-who" value={l.speaker} maxLength={40}
                  onChange={(e) => upd({
                    lines: view.lines.map((x, j) => (j === i ? { ...x, speaker: e.target.value } : x)),
                  })} />
                <input value={l.text} maxLength={120}
                  onChange={(e) => upd({
                    lines: view.lines.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)),
                  })} />
                <button className="btn-sm" onClick={() => upd({ lines: view.lines.filter((_, j) => j !== i) })}>✕</button>
              </div>
            ))}
            <button className="btn-sm" onClick={() => upd({ lines: [...(view.lines ?? []), { speaker: "旁白", text: "" }] })}>+ 加一句</button>
          </div>
          {(view.shots ?? []).map((s) => (
            <div key={s.seq} className="field-full sub-summary">
              <div className="card-head mb-2"><b>镜头 {s.seq}</b></div>
              <div className="clip-edit-grid">
                <input value={s.scene_name} maxLength={40} placeholder="场景"
                  onChange={(e) => updShot(s.seq, { scene_name: e.target.value })} />
                <input value={s.shot_type} maxLength={20} placeholder="景别"
                  onChange={(e) => updShot(s.seq, { shot_type: e.target.value })} />
                <input value={s.camera} maxLength={20} placeholder="运镜"
                  onChange={(e) => updShot(s.seq, { camera: e.target.value })} />
                <input type="number" min={1} max={8} value={s.duration_s} placeholder="秒"
                  onChange={(e) => updShot(s.seq, { duration_s: Math.max(1, Math.min(8, Number(e.target.value) || 1)) })} />
              </div>
              <textarea rows={2} value={s.action_desc} placeholder="画面(40 字内,必须可画)"
                onChange={(e) => updShot(s.seq, { action_desc: e.target.value })} />
              <input value={s.dialogue} maxLength={200} placeholder="该镜头台词(可空)"
                onChange={(e) => updShot(s.seq, { dialogue: e.target.value })} />
              <textarea rows={3} value={s.prompt_cn} placeholder="中文提示词(含画风锚)"
                onChange={(e) => updShot(s.seq, { prompt_cn: e.target.value })} />
              <textarea rows={2} value={s.prompt_en} placeholder="英文提示词"
                onChange={(e) => updShot(s.seq, { prompt_en: e.target.value })} />
            </div>
          ))}
        </div>
      ) : (
        <>
          {card.hook_text && <p className="hint"><b>投流钩子:</b>{card.hook_text}</p>}
          {card.logline && <p className="hint">{card.logline}</p>}
          {card.emotion_curve && <p className="hint"><b>情绪曲线:</b>{card.emotion_curve}</p>}
          {card.quote_source && <p className="hint"><b>金句原句(正文):</b>{card.quote_source}</p>}
          {card.cautions?.length > 0 && <div className="msg-err">⚠ {card.cautions.join(";")}</div>}
          {card.lines?.length > 0 && (
            <div className="sub-summary">
              {card.lines.map((l, i) => (
                <div key={i} className="script-line"><b>{l.speaker}</b>:{l.text}
                  {l.action && <span className="muted">(画面:{l.action})</span>}
                </div>
              ))}
            </div>
          )}
          <div className="card-head mb-2">
            <b>分镜({card.shots.length} 格 · {card.shots.reduce((s, x) => s + x.duration_s, 0)}s)</b>
            <span className="muted">画风锚已注入每格提示词</span>
          </div>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>#</th><th>场景</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr></thead>
              <tbody>
                {card.shots.map((s) => (
                  <tr key={s.seq}>
                    <td>{s.seq}</td><td>{s.scene_name}</td><td>{s.shot_type}</td>
                    <td>{s.camera}</td><td>{s.duration_s}</td>
                    <td>{s.action_desc}</td><td>{s.dialogue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {card.shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <div key={s.seq} className="sub-summary">
              <div className="card-head mb-2"><b>镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)</b></div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">中文提示词</span><CopyBtn text={s.prompt_cn} /></div>
                <textarea rows={3} readOnly value={s.prompt_cn} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">英文提示词</span><CopyBtn text={s.prompt_en} /></div>
                <textarea rows={2} readOnly value={s.prompt_en} />
              </div>
            </div>
          ))}
          {card.chunks?.length > 0 && (
            <>
              <div className="card-head mb-2"><b>生成切段(一段一次生成,画布拼接)</b></div>
              {card.chunks.map((c) => (
                <div key={c.index} className="hint">
                  <b>段 {c.index}</b>({c.start_s}-{c.end_s}s · 镜头 {c.shot_seqs.join("、")})
                  {c.over_limit && <span className="warn-tip"> ⚠超限</span>}
                  {c.subtitle && <span className="muted"> 字幕:{c.subtitle.replace(/\n/g, " / ")}</span>}
                </div>
              ))}
            </>
          )}
          <p className="hint">出片:按段生成 → 画布拼接 → 压 SRT → 末格加金句字幕卡「{card.punchline}」。</p>
          {/* 15s 短片常常一段就出完,这时模型自带音频直接可用;要拼接才需要自己配人声。
              恰好一段才算(===1):还没切段(0)时按分轨口径说,别把「没切」当「一段出完」 */}
          <p className="hint">
            {(card.chunks?.length ?? 0) === 1
              ? "音频:整片一段出完,不存在段间错位——直接用模型自带的音频最省事;只有金句要一字不差时才自己配一条人声。"
              : "音频:环境音留给模型出,人声与 BGM 整片后期铺(分段各自带人声与音乐,拼接处必然断)。"}
          </p>
        </>
      )}
    </section>
  );
}
