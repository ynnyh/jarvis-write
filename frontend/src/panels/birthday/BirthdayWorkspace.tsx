// 单条工作台:批产 → 三选一 → 定手卡 → 出片,一条线性管线。
// 与 clips 工作台同构(串行管线 + StepBar 步骤条锚点 + job 重挂),差异只在
// 输入是寿星资料、文案口径是祝福片;参数编辑器改的是「拍谁」,保存后重跑才生效。
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { BirthdayWish, WishCard, birthdayApi } from "../../birthdayApi";
import { api } from "../../api";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg, pollJob } from "../../pollJob";
import Banner from "../../ui/Banner";
import StepBar, { Step } from "../../ui/StepBar";
import BirthdayParamsEditor from "./BirthdayParamsEditor";
import BirthdayHandcard from "./BirthdayHandcard";
import ShootWorkbench from "./BirthdayShoot";
import { wishStatusTone } from "./shared";

export default function BirthdayWorkspace({ wid }: { wid: number }) {
  const nav = useNavigate();
  const location = useLocation() as { state?: { autostart?: boolean } };
  const { run } = useJob();
  const [row, setRow] = useState<BirthdayWish | null>(null);
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
    try { setRow((await birthdayApi.get(wid)).wish_row); }
    catch (e) { setErr(errMsg(e)); }
  }, [wid]);
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
        // 先看有没有跑着的任务(切页回来恢复);再处理建单后的自动开跑
        const { jobs } = await api.myJobs();
        hit = jobs.find((j) => j.kind === `birthday-gen-${wid}` || j.kind === `birthday-reexp-${wid}`);
      } catch { /* 任务列表拉不到就静默,不挡页面 */ }
      if (hit) { await attach(hit.job_id); return; }
      if (location.state?.autostart && row.status === "draft") {
        try {
          const r = await birthdayApi.generate(wid);
          await attach(r.job_id);
        } catch (e) { setErr(errMsg(e)); }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row, wid]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      await run(() => birthdayApi.generate(wid, feedback.trim() || undefined),
        { kind: `birthday-gen-${wid}`, onStage: setStage });
      await reload();
      toast.ok("三个本子已出", "点开卡片看分镜再选;不合适带意见换一批");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function reroll(index: number) {
    setBusy(true); setErr(""); setStage("");
    try {
      await run(() => birthdayApi.reexpand(wid, index, rerollFeedback.trim() || undefined),
        { kind: `birthday-reexp-${wid}`, onStage: setStage });
      await reload();
      toast.ok("重拍完成", "切入与画风不变,分镜已换");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); setRerollIdx(-1); setRerollFeedback(""); }
  }

  async function pick(index: number) {
    try {
      const r = await birthdayApi.pick(wid, index);
      setRow(r.wish_row);
      toast.ok("已选定", "手卡下方可导出,细节不顺眼可直接编辑");
    } catch (e) { toast.err("选择失败", errMsg(e)); }
  }

  async function exp(fmt: "md" | "srt" | "json") {
    try { await birthdayApi.export(wid, fmt); } catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  async function saveCard(card: WishCard) {
    const r = await birthdayApi.saveCard(wid, card);
    setRow(r.wish_row);
  }

  if (row === null && !err) return <p className="muted">加载中…</p>;
  if (row === null) return <div className="msg-err">{err}</div>;

  const candidates = row.candidates ?? [];
  const chosen = row.chosen >= 0 ? (row.clip as WishCard) : null;

  const steps: Step[] = [
    { key: "pick", label: "选本子", done: candidates.length > 0,
      todo: candidates.length === 0
        ? "点「产 3 个本子」——AI 按寿星资料一次给三个不同切入,展开看分镜再选。"
        : "浏览三个本子,展开看分镜与回忆点落实情况,「选定」看中的那个;切入不合适可带意见换一批、或单条重拍。" },
    { key: "handcard", label: "定手卡", done: !!chosen,
      todo: chosen
        ? "对照寿星资料卡核对回忆点是否落实,不顺眼点「✍ 编辑」;敲定后进③出片。"
        : "先在①选定一个本子,手卡与出片盘才会出现在这里。" },
    { key: "shoot", label: "出片", done: shootProg.total > 0 && shootProg.done >= shootProg.total,
      todo: chosen
        ? `逐段出片:回忆杀段传寿星真实照片走图生视频,普通段直接粘提示词,成片贴回「成品链接」存档。进度 ${shootProg.done}/${shootProg.total}。`
        : "先定手卡,切段就绪后在这里逐段出片。" },
  ];

  return (
    <>
      <div className="page-head">
        <h1>
          {row.honoree_name || "寿星"}的生日片
          {row.pack_label && <span className="badge mute">{row.pack_label}</span>}
          <span className="badge mute">{row.tone_display}</span>
          <span className="badge mute">{row.duration_s}s</span>
          {!row.pack_label && <span className="badge mute">{row.direction_label}</span>}
          <span className={`badge ${wishStatusTone(row.status)}`.trim()}>{row.status_cn}</span>
        </h1>
        <button className="btn" onClick={() => nav("/birthday")}>← 返回</button>
      </div>

      <StepBar steps={steps} anchorPrefix="bday-step" allDone={<>
        挑定了本子、手卡就绪、整片按段也全部出完 👏 寿星看到这条片的时候,就是它生效的时候。
      </>} />

      {!busy && (
        <details className="clip-params">
          <summary className="fld-hint" style={{ cursor: "pointer" }}>
            寿星资料与参数(称呼/回忆点/时长/画风——改完保存,对下一次生成生效)
          </summary>
          <div style={{ marginTop: 8 }}>
            <BirthdayParamsEditor row={row} onSaved={setRow} />
          </div>
        </details>
      )}

      <section className="card" id="bday-step-pick">
        <div className="card-head">
          <h3 className="grow">{candidates.length ? "三个本子,点卡选定" : "生成"}</h3>
          <button className="primary" disabled={busy} onClick={generate}>
            {candidates.length ? "换一批" : "产 3 个本子"}
          </button>
        </div>
        {candidates.length > 0 && (
          <div className="field">
            <label className="fl" htmlFor="bday-feedback">
              换一批的意见<span className="hint">可选:这批哪里不满意,下一批照着调</span>
            </label>
            <input id="bday-feedback" value={feedback} maxLength={200}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="如「整蛊力度不够,再狠一点;回忆点只用天台那条」" />
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
            {c.punchline && <div><b>祝福金句:</b>{c.punchline}</div>}
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
                      placeholder="如「回忆杀再加一格,台词砍半」" />
                    <div className="form-actions">
                      <button className="primary btn-sm" disabled={busy} onClick={() => void reroll(i)}>开拍</button>
                      <button className="btn-sm" onClick={() => { setRerollIdx(-1); setRerollFeedback(""); }}>取消</button>
                    </div>
                  </div>
                ) : (
                  <button className="btn-sm" disabled={busy} onClick={() => setRerollIdx(i)}>↻ 重拍这条(回忆没落实时)</button>
                )}
              </div>
            )}
          </div>
        ))}
      </section>

      {chosen && (
        <div id="bday-step-handcard">
          <BirthdayHandcard card={chosen} honoreeName={row.honoree_name}
            memories={row.memories ?? []} onExport={exp} onSave={saveCard} />
        </div>
      )}

      {chosen && (
        <div id="bday-step-shoot">
          <ShootWorkbench wishId={wid} card={chosen} onProgress={onShootProg} />
        </div>
      )}
    </>
  );
}
