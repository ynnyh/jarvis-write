// 情绪短片工坊:15/30 秒命题短视频,一次产三个本子三选一(快节奏,不磨单版)。
// 双入口共用:书房「情绪短片」(通用命题)与小说项目「投流」页签(小说衍生,金句溯源)。
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ClipCard, ClipDirection, ClipTheme, MoodClip, clipsApi } from "../clipsApi";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import Banner from "../ui/Banner";
import { CopyBtn } from "../ui/copy";
import { confirmDialog } from "../ui/ConfirmDialog";

function useClipsMeta() {
  const [meta, setMeta] = useState<{ themes: ClipTheme[]; durations: number[]; directions: ClipDirection[] } | null>(null);
  useEffect(() => { void clipsApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

export default function ClipsPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const projectId = params.get("project");
  return id ? <ClipWorkspace cid={Number(id)} /> : <ClipsList projectId={projectId ? Number(projectId) : null} />;
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
      });
      toast.ok("已建,正在产三个本子", "三选一,不合适整版换");
      nav(`/clips/${r.clip_row.id}`);
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setBusy(false); }
  }

  const novelMode = projectId !== null;

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
          <div className="field">
            <label className="fl" htmlFor="clip-direction">画风</label>
            <select id="clip-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
              {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </select>
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="clip-inspire">
              一句话灵感<span className="hint">可选</span>
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
          <span className="form-actions-tip">三个本子切入各不相同;都不满意可以整批换。</span>
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
function ClipWorkspace({ cid }: { cid: number }) {
  const nav = useNavigate();
  const { run } = useJob();
  const [row, setRow] = useState<MoodClip | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    try { setRow((await clipsApi.get(cid)).clip_row); }
    catch (e) { setErr(errMsg(e)); }
  }, [cid]);
  useEffect(() => { void reload(); }, [reload]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      await run(() => clipsApi.generate(cid), { kind: `clips-gen-${cid}`, onStage: setStage });
      await reload();
      toast.ok("三个本子已出", "点一张卡选定;都不满意就「换一批」");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function pick(index: number) {
    try {
      const r = await clipsApi.pick(cid, index);
      setRow(r.clip_row);
      toast.ok("已选定", "手卡下方可导出,想换就重新生成");
    } catch (e) { toast.err("选择失败", errMsg(e)); }
  }

  async function exp(fmt: "md" | "srt" | "json") {
    try { await clipsApi.export(cid, fmt); } catch (e) { toast.err("导出失败", errMsg(e)); }
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
          <span className="badge">{row.status_cn}</span>
        </h1>
        <button className="btn" onClick={() => nav(row.source_project_id ? `/project/${row.source_project_id}/book?tab=clips` : "/clips")}>← 返回</button>
      </div>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">{candidates.length ? "三个本子,点卡选定" : "生成"}</h3>
          <button className="primary" disabled={busy} onClick={generate}>
            {candidates.length ? "换一批" : "产 3 个本子"}
          </button>
        </div>
        {busy && <Banner stage={stage} text="AI 正在产三个本子…" />}
        {err && <div className="msg-err">{err}</div>}
        {candidates.map((c, i) => (
          <div key={i} className={"sub-summary ep-row" + (row.chosen === i ? " ep-on" : "")}>
            <div className="card-head mb-2">
              <b>本子 {i + 1} · {c.take}</b>
              {c.hook_text && <span className="badge">钩子:{c.hook_text}</span>}
              <span className="grow" />
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
          </div>
        ))}
      </section>

      {chosen && (
        <ClipHandcard card={chosen} onExport={exp} />
      )}
    </>
  );
}

/** 手卡:选定本子的完整物料(分镜/三轨/金句/切段),通用页与小说面板共用 */
export function ClipHandcard({ card, onExport }: {
  card: ClipCard;
  onExport: (fmt: "md" | "srt" | "json") => void;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">手卡 · 《{card.take}》</h3>
        <CopyBtn text={card.punchline} label="复制金句" />
        <button className="btn-sm" onClick={() => onExport("md")}>导出手卡</button>
        <button className="btn-sm" onClick={() => onExport("srt")}>字幕SRT</button>
        <button className="btn-sm" onClick={() => onExport("json")}>JSON</button>
      </div>
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
    </section>
  );
}
