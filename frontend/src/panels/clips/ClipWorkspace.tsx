// 单条工作台:批产 → 三选一 → 定手卡 → 出片,一条线性管线。
// 为什么这里套 StepBar 而不套 wb-cols 双栏:宣传片/漫剧是「锚资产常驻侧栏 + 推进主区」的
// 并行轨工作台;clips 是串行的「出本子→定手卡→出片」,没有要随时回头看的锚资产,强拆双栏
// 反而多一层空侧栏。统一的是「现在该点哪个按钮」的步骤条心智——与另两条出片线共用 StepBar。
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ClipCard, MoodClip, clipsApi } from "../../clipsApi";
import { api } from "../../api";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg, pollJob } from "../../pollJob";
import Banner from "../../ui/Banner";
import StepBar, { Step } from "../../ui/StepBar";
import ClipParamsEditor from "./ClipParamsEditor";
import ClipHandcard from "./ClipHandcard";
import ShootWorkbench from "./ClipShoot";
import { clipStatusTone } from "./shared";

export default function ClipWorkspace({ cid, mode = "mood" }: { cid: number; mode?: string }) {
  const nav = useNavigate();
  const location = useLocation() as { state?: { autostart?: boolean; backTo?: string } };
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
  // 出片进度:③出片步的 done 判断依据;ShootWorkbench 挂载后回传,未挂=0
  const [shootProg, setShootProg] = useState({ done: 0, total: 0 });
  const onShootProg = useCallback((done: number, total: number) => setShootProg({ done, total }), []);
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

  const steps: Step[] = [
    { key: "pick", label: "选本子", done: candidates.length > 0,
      todo: candidates.length === 0
        ? "点「产 3 个本子」——AI 一次给三个不同切入,展开看分镜再选。"
        : "浏览三个本子,展开看分镜,「选定」看中的那个;切入不合适可带意见换一批、或单条重拍。" },
    { key: "handcard", label: "定手卡", done: !!chosen,
      todo: chosen
        ? "核对金句/分镜/三轨提示词,不顺眼点「✍ 编辑」;敲定后进③出片。"
        : "先在①选定一个本子,手卡与出片盘才会出现在这里。" },
    { key: "shoot", label: "出片", done: shootProg.total > 0 && shootProg.done >= shootProg.total,
      todo: chosen
        ? `逐段出片:每段先传角色参考图、再粘段提示词到图文生视频工具,成片贴回「成品链接」存档。进度 ${shootProg.done}/${shootProg.total}。`
        : "先定手卡,切段就绪后在这里逐段出片。" },
  ];

  return (
    <>
      <div className="page-head">
        <h1>
          {row.theme_display}{row.custom_theme && !row.theme && row.mode !== "free" ? `·${row.custom_theme}` : ""}
          <span className="badge mute">{row.duration_s}s</span>
          <span className="badge mute">{row.direction_label}</span>
          <span className={`badge ${clipStatusTone(row.status)}`.trim()}>{row.status_cn}</span>
        </h1>
        {/* 返回跟进入路径走(列表/小说页签进来时带了 backTo):从哪个列表点进来
            就回哪个列表。刷新/直链丢状态时才按数据归属兜底——小说衍生企划回
            小说投流页签,工坊自建回工坊列表。旧实现无条件按数据归属跳,独立
            工坊点进小说衍生企划后返回被甩进小说书页,工坊上下文凭空消失。 */}
        <button className="btn" onClick={() => nav(
          location.state?.backTo
          ?? (row.source_project_id
            ? `/project/${row.source_project_id}/book?tab=clips`
            : `/${mode === "play" ? "inspire" : mode === "free" ? "free" : "clips"}`),
        )}>← 返回</button>
      </div>

      <StepBar steps={steps} anchorPrefix="clips-step" allDone={<>
        挑定了本子、手卡就绪、整片按段也全部出完 👏 要换角度就回①带意见换一批,或回②编辑手卡。
      </>} />

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

      <section className="card" id="clips-step-pick">
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
        <div id="clips-step-handcard">
          <ClipHandcard card={chosen} onExport={exp} onSave={saveCard} />
        </div>
      )}

      {chosen && (
        <div id="clips-step-shoot">
          <ShootWorkbench clipId={cid} card={chosen} onProgress={onShootProg} />
        </div>
      )}
    </>
  );
}