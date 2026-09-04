import { useCallback, useEffect, useState } from "react";
import type { DramaBoardResult, DramaEpisode, DramaProductionPack, DramaShot } from "../../dramaApi";
import { DRAMA_STATUS_CN, dramaApi } from "../../dramaApi";
import type { PrevFrameInfo } from "../../renderApi";
import { renderApi } from "../../renderApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { FilmPromptCard } from "../../ui/FilmPromptCard";
import { nextEpisodeTodo } from "./dramaShared";
import { PromptRow } from "./PromptRow";
import { ClipPlanSection } from "./ClipPlanSection";
import { SynthSection } from "./SynthSection";

// ================= 单集详情:剧本 → 分镜 → 提示词 → 导出 =================
export function EpisodeDetail({ pid, eid, hasStyle, ratio, renderMode, onEpisodesChanged, onShootProgress, onDeselect }: {
  pid: number; eid: number; hasStyle: boolean; ratio: string; renderMode: string;
  onEpisodesChanged: (eps: DramaEpisode[]) => void;
  /** 把「视频出好了 N/M」上报给顶部步骤条(逐格出片那一步的状态) */
  onShootProgress?: (prog: { done: number; total: number }) => void;
  onDeselect: () => void;
}) {
  const { run } = useJob();
  const [episode, setEpisode] = useState<DramaEpisode | null>(null);
  const [shots, setShots] = useState<DramaShot[]>([]);
  const [pack, setPack] = useState<DramaProductionPack | null>(null);
  const [busy, setBusy] = useState(""); // script | board | prompts | pack | ""
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  // 本集重点(作者改编意图)草稿:随集加载初始化,失焦且改动才落库——
  // 写/重写剧本时会作为高优先级指令注入,重写不丢
  const [focusDraft, setFocusDraft] = useState("");
  // 拆分镜的如实交代(被截断 / 总时长短于目标),留在页面上直到下次重拆
  const [boardNotice, setBoardNotice] = useState("");
  // 末帧接力候选:seq → 上一格末帧(整集一次拉齐;出片后重拉,下一格就有候选了)
  const [prevFrames, setPrevFrames] = useState<Record<number, PrevFrameInfo>>({});

  const reload = useCallback(async () => {
    try {
      const [r, pk] = await Promise.all([
        dramaApi.getEpisode(pid, eid),
        dramaApi.getPack(pid, eid),
      ]);
      setEpisode(r.episode);
      setShots(r.shots);
      setPack(pk.pack);
    } catch (e) { setErr(errMsg(e)); }
    // 出过片的格才有末帧;拉取失败静默(接力是可选增强,别让它把分镜页搞红)
    try {
      const { by_seq } = await renderApi.episodePrevFrames(pid, eid);
      setPrevFrames(Object.fromEntries(
        Object.entries(by_seq).map(([k, v]) => [Number(k), v]),
      ));
    } catch { setPrevFrames({}); }
  }, [pid, eid]);

  useEffect(() => { void reload(); }, [reload]);

  // 切集/换集时把「本集重点」草稿对齐到当前集
  useEffect(() => { setFocusDraft(episode?.focus ?? ""); }, [episode?.id, episode?.focus]);

  // 本集重点失焦保存:有实质变化才 PATCH;成功把返回的集写回本地(输入框不闪)
  async function saveFocus() {
    if (!episode) return;
    const v = focusDraft.trim();
    if (v === (episode.focus || "")) return;
    try {
      const r = await dramaApi.patchEpisode(pid, episode.id, { focus: v });
      setEpisode(r.episode);
      toast.ok("本集重点已保存", "写/重写剧本时会优先遵循");
    } catch (e) { setErr(errMsg(e)); }
  }

  // 出片进度上报:shots 变了(挂图/打勾/出片回写)就把「视频 N/M」递给顶部步骤条
  useEffect(() => {
    if (!onShootProgress) return;
    onShootProgress({
      done: shots.filter((s) => s.done_video).length,
      total: shots.length,
    });
  }, [shots, onShootProgress]);

  async function refreshList() {
    try { onEpisodesChanged((await dramaApi.getEpisodes(pid)).episodes); } catch { /* 列表刷新失败不阻塞 */ }
  }

  async function act(kind: "script" | "board" | "prompts" | "pack", start: () => Promise<{ job_id: string }>, okTitle: string) {
    setBusy(kind); setErr(""); setStage("");
    try {
      const r = await run<unknown>(start, { kind: `drama-${kind}-${eid}`, onStage: setStage });
      if (kind === "board") setBoardNotice((r as DramaBoardResult | null)?.notice || "");
      await reload();
      await refreshList();
      toast.ok(okTitle);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); setStage(""); }
  }

  async function exp(fmt: "md" | "csv" | "json" | "pack" | "srt") {
    try { await dramaApi.exportEpisode(pid, eid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  if (episode === null && !err) return <p className="muted">加载中…</p>;

  const hasScript = !!episode?.script?.lines?.length;
  const sourceLabel = episode?.source_label || `第 ${episode?.source_chapter} 章`;
  const boardTotalS = shots.reduce((s, x) => s + x.duration_s, 0);
  // 施工进度就地算(不等接口回):挂静帧/打勾都是即时改本地 shots,重取一次反而慢半拍
  const stillsDone = shots.filter((s) => s.done_still).length;
  const videosDone = shots.filter((s) => s.done_video).length;
  // 段计划的重算信号:并段只看场景/角色/时长/台词这几栏,别的栏改了不必重取。
  // 静帧挂没挂上也要带——段表的「首帧图已就位」直接读它。
  const clipSig = shots
    .map((s) => `${s.seq}:${s.scene_name}:${s.characters.join(",")}:${s.duration_s}`
      + `:${s.dialogue ? 1 : 0}:${s.motion_cn || ""}:${s.done_still ? 1 : 0}${s.assets?.length ?? 0}`)
    .join("|");

  // 「复制全部中文」:把这一集每格的中文提示词一口气拼起来(镜头号开头,格与格空行隔开)。
  // 用户分工:中文提示词去即梦/可灵出图,几十格一格格复制太磨人;这份整集清单粘到
  // 文档/交给模型/批量出图都能用。其余轨道(英/负/运动)刻意不带,保持出图主链路干净。
  const allCnText = shots
    .filter((s) => (s.prompt_cn || "").trim())
    .map((s) => `镜头 ${s.seq}：${(s.prompt_cn || "").trim()}`)
    .join("\n\n");

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">
          ④ 第 {episode?.ep_index} 集《{episode?.title}》
          <span className="badge">源:{sourceLabel}</span>
          <span className="badge">{episode ? (DRAMA_STATUS_CN[episode.status] ?? episode.status) : ""}</span>
        </h3>
        <button className="btn-sm" onClick={onDeselect}>收起</button>
      </div>
      {err && <div className="msg-err">{err}</div>}

      {/* 本集重点(改编意图):失焦保存;写/重写剧本时高优先级注入 */}
      <div className="field mb-2">
        <label className="fl" htmlFor="ep-focus">本集重点(可选,写剧本时优先遵循)</label>
        <input id="ep-focus" type="text" maxLength={200} value={focusDraft}
          placeholder="如:重点拍那场对峙;雨夜追逐要占半集"
          onChange={(e) => setFocusDraft(e.target.value)}
          onBlur={() => { void saveFocus(); }} />
      </div>

      <div className="card-head mb-2 ep-actions">
        <button className="primary" disabled={!!busy}
          title={`按钩子/卡点把${sourceLabel}的正文写成台词稿`}
          onClick={() => act("script", () => dramaApi.writeScript(pid, eid), "剧本已生成")}>
          {hasScript ? "重写剧本" : "④-1 写剧本"}
        </button>
        <button className="primary" disabled={!!busy || !hasScript}
          title={hasScript ? "把台词摊成一格格可画的画面(会覆盖旧分镜)" : "先写剧本"}
          onClick={() => act("board", () => dramaApi.storyboard(pid, eid), "分镜已生成(旧分镜已覆盖)")}>
          {shots.length ? "重新拆分镜" : "④-2 拆分镜"}
        </button>
        <button className="primary" disabled={!!busy || shots.length === 0}
          title={shots.length ? "给每格出中文/英文/负面三轨绘图提示词" : "先拆分镜"}
          onClick={() => act("prompts", () => dramaApi.prompts(pid, eid), "三轨提示词已生成")}>
          ④-3 出提示词
        </button>
        <button className="primary" disabled={!!busy || shots.length === 0}
          title={shots.length ? "出配音稿 + 剪辑清单(剪映照着走)" : "先拆分镜"}
          onClick={() => act("pack", () => dramaApi.buildPack(pid, eid), "成片包已生成(配音稿 + 剪辑清单)")}>
          {pack ? "重建成片包" : "④-4 出成片包"}
        </button>
        <span className="grow" />
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("md")}>导出手册</button>
        <button className="btn-sm" disabled={!pack} onClick={() => exp("pack")}>成片包</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("srt")}>字幕SRT</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("csv")}>CSV</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("json")}>JSON</button>
      </div>
      {/* 现在该点哪个 / 为什么点不动:按状态只说一句 */}
      {!busy && <p className="hint wb-next">{episode ? nextEpisodeTodo(episode) : ""}
        {!hasScript && <> 剧本忠实改编<b>{sourceLabel}</b>的定稿正文(超长的章保头尾、中段节选),那几章没定稿就会报错。</>}
        {shots.length > 0 && !hasStyle && <> ⚠ 还没定美术风格卡,「出提示词」会被拦下——先回 ① 定画风。</>}
      </p>}
      {busy && <Banner stage={stage} text="AI 正在处理…" />}
      {boardNotice && !busy && <div className="notice notice-warn">分镜说明:{boardNotice}</div>}

      {/* 剧本 */}
      {episode?.script?.lines?.length ? (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>剧本({episode.script.lines.length} 条)</b>
            <span className="muted">{episode.script.synopsis}</span></div>
          {episode.script.lines.map((l, i) => (
            <div key={i} className="script-line">
              <b>{l.speaker}</b>:{l.text}
              {l.action && <span className="muted">(画面:{l.action})</span>}
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">先「写剧本」:按开场钩子开场、结尾卡点收束,台词口语化、每句可拍。</p>
      )}

      {/* 分镜表 */}
      {shots.length > 0 && (
        <>
          <div className="card-head mb-2">
            <b>分镜表({shots.length} 格 · 约 {boardTotalS} 秒)</b>
            <span className="muted">
              目标 {episode?.duration_target_s}s
              {episode && boardTotalS < episode.duration_target_s * 0.8
                ? " · 偏短,可重拆或手动加时长" : ""}
            </span>
          </div>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>#</th><th>场景</th><th>角色</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr>
              </thead>
              <tbody>
                {shots.map((s) => (
                  <tr key={s.id}>
                    <td>{s.seq}</td>
                    <td>{s.scene_name}</td>
                    <td>{s.characters.join("、")}</td>
                    <td>{s.shot_type}</td>
                    <td>{s.camera}</td>
                    <td>{s.duration_s}</td>
                    <td>{s.action_desc}</td>
                    <td>{s.dialogue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 三轨提示词 + 逐格出片(步骤条「逐格出片」步的锚点落在这里) */}
      <div id="drama-step-shoot">
      {shots.some((s) => s.prompt_cn || s.prompt_en) && (
        <>
          <div className="card-head mb-2"><b>三轨提示词(即拿即用)</b>
            {/* 逐格施工单的进度条:一集几十格,出到第几格得一眼看见 */}
            <span className="badge">静帧 {stillsDone}/{shots.length}</span>
            <span className="badge">视频 {videosDone}/{shots.length}</span>
            <span className="grow" />
            <CopyBtn
              text={allCnText}
              label="复制全部中文"
              title="把这集所有镜头的中文提示词按镜头号一口气复制,去即梦/可灵批量出图"
            />
            <span className="muted">
              画风锚/角色锚已注入。复制中文提示词去即梦/可灵出图;出好的静帧挂回那一格,
              做完打个勾——导出的施工单会带上这份进度
            </span></div>
          {shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <PromptRow key={s.id} pid={pid} shot={s} ratio={ratio} prevFrame={prevFrames[s.seq]} onSaved={(ns) => {
              // 函数式更新:几十格挂图/打勾是连着点的,拿闭包里的旧 shots 算会把上一格的结果吞掉
              setShots((prev) => prev.map((x) => (x.id === ns.id ? ns : x)));
            }} onRegenerated={() => { void reload(); void refreshList(); }} />
          ))}
        </>
      )}
      </div>

      {/* 视频段计划:一次生成一段,再在画布里拼(治视频站的单次时长上限) */}
      {shots.length > 0 && <ClipPlanSection pid={pid} eid={eid} sig={clipSig} />}

      {/* 整片提示词(端到端音频原生视频模型):一条提示词出一整片,与逐格链互补 */}
      {shots.length > 0 && <FilmPromptSection pid={pid} eid={eid} />}

      {/* 一键合成(完整档):整集拼接 + BGM 垫底 + 字幕,ffmpeg 本地跑 */}
      {shots.length > 0 && renderMode === "full" && (
        <SynthSection pid={pid} eid={eid} shots={shots} onDone={reload} />
      )}

      {/* 成片包(阶段 2):配音稿 + 剪辑清单 */}
      {pack && (
        <>
          <div className="card-head mb-2">
            <b>成片包</b>
            <span className="muted">
              镜头 {pack.totals.shots} 格 · 分镜 {pack.totals.storyboard_s}s(目标 {pack.totals.target_s}s)
              · 配音估时 {pack.totals.voice_s}s
            </span>
          </div>
          {pack.dubbing.length > 0 && (
            <>
              <div className="card-head mb-2"><b>配音稿({pack.dubbing.length} 条)</b></div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>#</th><th>说话人</th><th>声线</th><th>朗读文本</th><th>估时/画面</th><th>选型</th></tr>
                  </thead>
                  <tbody>
                    {pack.dubbing.map((d) => (
                      <tr key={d.seq}>
                        <td>{d.seq}</td>
                        <td>{d.speaker}</td>
                        <td>{d.voice}</td>
                        <td>{d.tts_text}</td>
                        <td>{d.est_s}s / {d.shot_duration_s}s</td>
                        <td className="muted">{d.tts_hint}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {pack.narration_full && (
            <div className="sub-summary">
              <div className="card-head mb-2"><b>整段口播(粘给 TTS 一把梭)</b><CopyBtn text={pack.narration_full} /></div>
              <div className="script-line pre-wrap">{pack.narration_full}</div>
            </div>
          )}
          {pack.checklist.length > 0 && (
            <>
              <div className="card-head mb-2"><b>剪辑清单(按镜头顺序)</b></div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>#</th><th>场景</th><th>秒</th><th>字幕</th><th>转场</th><th>配乐</th><th>备注</th></tr>
                  </thead>
                  <tbody>
                    {pack.checklist.map((c) => (
                      <tr key={c.seq}>
                        <td>{c.seq}</td>
                        <td>{c.scene}</td>
                        <td>{c.duration_s}</td>
                        <td>{c.subtitle}</td>
                        <td>{c.transition}</td>
                        <td>{c.bgm_tag}</td>
                        <td className="muted">{c.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint">出片顺序:分镜提示词出图(即梦/可灵) → 图生视频/加轻动 → 按配音稿合成语音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM。</p>
            </>
          )}
        </>
      )}
    </div>
  );
}

/** 整片提示词(端到端音频原生视频模型用):Sora/Veo/可灵这类「一条提示词出一整片」。
 *
 *  与三轨提示词的分工:那份逐格出图逐格出片,人再拼;这份把分镜+角色卡+画风组装成
 *  一整段带时间码镜头表的成片提示词,贴进端到端模型一次出整集。UI 与生成/保存/复制
 *  逻辑在各工坊完全一致,收敛在共享的 FilmPromptCard,这里只接本工坊的数据源。
 */
function FilmPromptSection({ pid, eid }: { pid: number; eid: number }) {
  // 单段时长上限:外部模型多数单条 15s,少数支持 30s
  const [segS, setSegS] = useState<15 | 30>(15);
  return (
    <FilmPromptCard
      load={() => dramaApi.getFilmPrompt(pid, eid).then((r) => r.film_prompt)}
      save={(t) => dramaApi.saveFilmPrompt(pid, eid, t).then((r) => r.film_prompt)}
      generate={() => dramaApi.buildFilmPrompt(pid, eid, segS)}
      jobKind={`drama-film-prompt-${eid}`}
      readyHint="先拆分镜,才有原料组装整片提示词"
      generateDetail="文档已按段切好:每段单独复制生成,按段号拼接成集"
      headerExtra={
        <select value={segS} title="单段时长上限:外部模型单次生成的上限"
          onChange={(e) => setSegS(Number(e.target.value) as 15 | 30)}
          style={{ padding: "2px 6px" }}>
          <option value={15}>单段 ≤15s</option>
          <option value={30}>单段 ≤30s</option>
        </select>
      }
    />
  );
}
