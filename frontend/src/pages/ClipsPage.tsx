// 情绪短片/灵感工坊入口(/clips 与 /inspire):列表与三选一工作台的路由分发 + 列表。
// 与宣传片工坊同一套组织:这里只留路由分发与列表;单条工作台(批产→三选一→手卡→出片)
// 拆在 panels/clips/ 下——页面文件不再是一坨上千行。列表件同时被小说项目「投流」页签复用。
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ClipCard, MoodClip, clipsApi } from "../clipsApi";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";
import ClipWorkspace from "../panels/clips/ClipWorkspace";
import { SteeringChips, useClipsMeta, clipStatusTone } from "../panels/clips/shared";

export default function ClipsPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const projectId = params.get("project");
  // 灵感工坊(/inspire)与情绪短片(/clips)共用这一个页面,按路径区分命题目录与文案
  const isInspire = useLocation().pathname.startsWith("/inspire");
  const mode = isInspire ? "play" : "mood";
  return id
    ? <ClipWorkspace cid={Number(id)} mode={mode} />
    : <ClipsList projectId={projectId ? Number(projectId) : null} mode={mode} />;
}

// ================= 列表 + 新建(通用/小说衍生共用) =================
export function ClipsList({ projectId, mode = "mood" }: { projectId: number | null; mode?: string }) {
  const nav = useNavigate();
  const meta = useClipsMeta();
  const isPlay = mode === "play";
  const base = isPlay ? "/inspire" : "/clips";
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
    try { setRows((await clipsApi.list(projectId ?? undefined, mode)).clips); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, [projectId, mode]);
  useEffect(() => { void reload(); }, [reload]);

  async function create() {
    const kind = isPlay ? "玩法" : "情绪主题";
    if (!theme && !custom.trim()) { toast.err("先选命题", `选一个${kind},或填自定义`); return; }
    setBusy(true);
    try {
      const r = await clipsApi.create({
        mode, theme: theme || undefined, custom_theme: custom.trim(), duration_s: duration,
        direction, inspiration: inspiration.trim(),
        source_project_id: projectId ?? undefined,
        dialogue_style: dialogueStyle, pacing, intensity,
        style_hints: styleHints.trim(),
      });
      toast.ok("已建,马上开产三个本子", "进工作台看进度;不合适可带意见换一批");
      // autostart:工作台 mount 时自动触发生成,补上"建完还要再点一次生成"的断档
      nav(`/${base}/${r.clip_row.id}`, { state: { autostart: true } });
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setBusy(false); }
  }

  const novelMode = projectId !== null;
  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);

  return (
    <>
      {!novelMode && (
        <div className="page-head">
          <h1>{isPlay ? "灵感工坊" : "情绪短片"}</h1>
          <button className="btn" onClick={() => nav("/")}>← 我的小说</button>
        </div>
      )}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            {novelMode ? "为这本书出投流短视频" : isPlay ? "新建灵感片" : "新建情绪短片"}
            <span className="muted">{novelMode ? "从书里抽金句名场面,金句可溯源" : "15/30 秒,一次三本子三选一"}</span>
          </h3>
        </div>
        <p className="card-desc">
          {novelMode
            ? "AI 读你的定稿章节,挑最戳人的金句与名场面,一次产 3 个不同切入的投流本子——核心金句必须出自正文,引擎会逐句溯源校验。"
            : isPlay
              ? "选个好玩/猎奇的灵感玩法(治愈手绘·黏土定格·赛博雨夜…),AI 一次给 3 个不同切入的本子,画风气质一眼可辨,允许荒诞与反差;每格带三轨提示词与切段,拿去即梦/剪映/minimax 直接出片。"
              : "选个情绪命题(遗憾/争吵/爱情/童趣…),AI 一次给 3 个不同切入的本子:钩子开场 → 情绪蓄势 → 金句收尾,每格带三轨提示词与切段,拿去即梦/剪映直接出片。"}
        </p>
        <div className="form-grid">
          {!novelMode && (
            <div className="field field-full">
              <span className="fl">{isPlay ? "灵感玩法" : "情绪命题"}<span className="hint">选一个,或用自定义</span></span>
              <div className="chips">
                {((isPlay ? meta?.plays : meta?.themes) ?? [])
                  .map((t) => (
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
              <label className="fl" htmlFor="clip-custom">{isPlay ? "自定义玩法" : "自定义主题"}</label>
              <input id="clip-custom" value={custom} maxLength={40}
                placeholder={isPlay ? "如「一只当上店长的猫」" : "如「毕业前夜」"}
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
              placeholder={novelMode
                ? "如「主打男女主第一次对峙」(不填则 AI 自动挑)"
                : isPlay ? "如「一个会说话的包子」(不填则 AI 自由发挥)" : "如「异地恋的最后一通电话」(不填则 AI 自由发挥)"} />
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
        <EmptyState>{novelMode ? "这本书还没有投流短视频——上面建一个。" : isPlay ? "还没有灵感片。选个玩法试试,三十秒出三个本子。" : "还没有短片。选个主题试试,三十秒出三个本子。"}</EmptyState>
      ) : rows.map((r) => (
        <div key={r.id} className="sub-summary ep-row"
          onClick={() => nav(`/${base}/${r.id}`)}>
          <div className="card-head mb-2">
            <b>{(r.clip as ClipCard).take ? `《${(r.clip as ClipCard).take}》` : ""}{r.theme_display}{r.custom_theme && !r.theme ? `·${r.custom_theme}` : ""}</b>
            <span className="badge mute">{r.duration_s}s</span>
            <span className="badge mute">{r.direction_label}</span>
            <span className={`badge ${clipStatusTone(r.status)}`.trim()}>{r.status_cn}</span>
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