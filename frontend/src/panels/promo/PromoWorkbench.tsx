// 宣传片工作台外壳:六步管线步骤条 + 「锚资产侧栏 / 推进主区」双栏(和漫剧共用 wb-* 外壳)。
// 为什么这么分栏:①素材点(事实红线)和③视觉锚(画风/地标)是「随时要回头看的锚」,
// 常驻侧栏;②研讨/④解说词/⑤分镜/⑥交付是「正在往下推的那几步」,占宽主区。
// 步骤条负责回答用户唯一的问题:「现在该点哪个按钮」。
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  PROMO_STATUS_CN, PromoAngle, PromoBrief, PromoDirection, PromoPlan, PromoShot, promoApi,
} from "../../promoApi";
import { errMsg } from "../../pollJob";
import StepBar, { Step } from "../../ui/StepBar";
import { FilmPromptCard } from "../../ui/FilmPromptCard";
import { usePromoJobs } from "./usePromoJobs";
import PlanForm from "./PlanForm";
import VisualSection from "./VisualSection";
import BriefSection from "./BriefSection";
import ScriptSection from "./ScriptSection";
import BoardSection from "./BoardSection";
import ChunksSection from "./ChunksSection";
import PackSection from "./PackSection";

export default function PromoWorkbench({ pid, meta }: {
  pid: number;
  meta: { angles: PromoAngle[]; directions: PromoDirection[] } | null;
}) {
  const nav = useNavigate();
  const [plan, setPlan] = useState<PromoPlan | null>(null);
  const [shots, setShots] = useState<PromoShot[]>([]);
  const [loadErr, setLoadErr] = useState("");
  // 整片提示词的单段时长上限:外部模型多数单条 15s,少数支持 30s
  const [segS, setSegS] = useState<15 | 30>(15);

  const reload = useCallback(async () => {
    try {
      const r = await promoApi.get(pid);
      setPlan(r.plan);
      setShots(r.shots);
      setLoadErr("");
    } catch (e) { setLoadErr(errMsg(e)); }
  }, [pid]);
  useEffect(() => { void reload(); }, [reload]);

  const jobs = usePromoJobs(pid, reload);

  if (plan === null && !loadErr) return <p className="muted">加载中…</p>;
  // 首屏加载失败不能是一页死胡同:给重试入口(成功路径也会清掉 loadErr,不让旧错误赖着)
  if (plan === null) return (
    <div className="msg-err">
      {loadErr}
      <div className="mt-2"><button className="btn" onClick={() => void reload()}>重试</button></div>
    </div>
  );

  const brief = plan.brief as PromoBrief;
  const hasBrief = !!brief?.positioning;
  const chunkCount = plan.chunks?.items?.length ?? 0;
  const hasPack = !!(plan.pack as { checklist?: unknown[] }).checklist?.length;

  const steps: Step[] = [
    { key: "plan", label: "素材点", done: !!plan.material_notes.trim(),
      todo: "把史实/数据/slogan 贴进①素材点——解说词只认这里的事实;实在没硬料可以先跳到②聊。" },
    { key: "brief", label: "研讨简报", done: hasBrief,
      todo: (plan.chat_log?.length ?? 0) === 0
        ? "去②和策划总监聊方向:说你的倾向,他会反问、给建议。"
        : "聊透了就点②的「收敛成创作简报」——后面每一步都按这份契约执行。" },
    { key: "visual", label: "视觉锚", done: !!plan.style_cn,
      todo: "③生成视觉风格:每格提示词都要逐字嵌这张画风卡,不然一格一个画风。" },
    { key: "script", label: "解说词", done: (plan.script?.lines?.length ?? 0) > 0,
      todo: "④写解说词:按简报段落推进,事实只用素材点。" },
    { key: "board", label: "分镜提示词", done: shots.length > 0 && shots.some((s) => s.prompt_cn),
      todo: shots.length === 0
        ? "⑤先「拆分镜」把解说词摊成一格格画面。"
        : "⑤点「出提示词」,每格出中英静帧提示词(嵌画风锚与地标锚)。" },
    { key: "deliver", label: "切段与成片包", done: chunkCount > 0 && hasPack,
      todo: chunkCount === 0
        ? "⑥-1 按视频站的时长档位切段,一段一次生成,出完拖上画布拼。"
        : "⑥-2 出成片包:配音稿 + 带转场/配乐的剪辑清单,再导出拍摄手册。" },
  ];

  return (
    <div className="wb-shell">
      <div className="page-head">
        <h1>
          {plan.title || plan.subject}
          <span className="badge">{PROMO_STATUS_CN[plan.status] ?? plan.status}</span>
        </h1>
        <button className="btn" onClick={() => nav("/promo")}>← 企划列表</button>
      </div>

      <StepBar steps={steps} anchorPrefix="promo-step" allDone={<>
        六步都走完了 👏 导出拍摄手册照着出图,按⑥-1 的段一段段生成视频,拖上画布拼起来;
        想换个角度重做,回②继续研讨、重新收敛简报即可。
      </>} />

      {/* 任务错误集中显示一次:六步共用一条流水线闸门,不必各区各报一遍 */}
      {jobs.err && <div className="msg-err">{jobs.err}</div>}

      <div className="wb-cols">
        <aside className="wb-rail">
          <div id="promo-step-plan">
            <PlanForm pid={pid} plan={plan} meta={meta} onSaved={setPlan} />
          </div>
          <div id="promo-step-visual">
            <VisualSection plan={plan} hasBrief={hasBrief} jobs={jobs}
              onStyle={() => void jobs.act("style", () => promoApi.style(pid), "视觉风格已定")}
              onLandmarks={() => void jobs.act("landmarks", () => promoApi.landmarks(pid), "地标卡已生成")} />
          </div>
        </aside>

        <div className="wb-main">
          <div id="promo-step-brief">
            <BriefSection pid={pid} plan={plan} jobs={jobs} onPlan={setPlan}
              onDistill={() => void jobs.act("brief", () => promoApi.brief(pid),
                "简报已收敛——核对后锁定,再往下走")} />
          </div>
          <div id="promo-step-script">
            <ScriptSection plan={plan} hasBrief={hasBrief} jobs={jobs}
              onWrite={() => void jobs.act("script", () => promoApi.script(pid), "解说词已生成")} />
          </div>
          <div id="promo-step-board">
            <BoardSection plan={plan} shots={shots} jobs={jobs}
              onBoard={() => void jobs.act("board", () => promoApi.storyboard(pid),
                "分镜已生成(旧分镜已覆盖)")}
              onPrompts={() => void jobs.act("prompts", () => promoApi.prompts(pid), "三轨提示词已生成")} />
          </div>
          <div id="promo-step-deliver">
            <ChunksSection plan={plan} shotCount={shots.length} jobs={jobs}
              onBuild={(cs) => void jobs.act("chunks", () => promoApi.chunks(pid, cs),
                `切段已生成(每段 ≤${cs}s)`)} />
            {/* 整片提示词(端到端音频原生视频模型):按单段上限切好段,逐段生成后拼接 */}
            <FilmPromptCard
              load={() => promoApi.getFilmPrompt(pid).then((r) => r.film_prompt)}
              save={(t) => promoApi.saveFilmPrompt(pid, t).then((r) => r.film_prompt)}
              generate={() => promoApi.buildFilmPrompt(pid, segS)}
              jobKind={`promo-film-prompt-${pid}`}
              ready={shots.length > 0}
              readyHint="先生成分镜,才有原料组装整片提示词"
              generateDetail="文档已按段切好:每段单独复制生成,按段号拼接成片"
              headerExtra={
                <select value={segS} title="单段时长上限:外部模型单次生成的上限"
                  onChange={(e) => setSegS(Number(e.target.value) as 15 | 30)}
                  style={{ padding: "2px 6px" }}>
                  <option value={15}>单段 ≤15s</option>
                  <option value={30}>单段 ≤30s</option>
                </select>
              }
            />
            <PackSection pid={pid} plan={plan} shotCount={shots.length} jobs={jobs}
              onBuild={() => void jobs.act("pack", () => promoApi.pack(pid), "成片包已生成")} />
          </div>
        </div>
      </div>
    </div>
  );
}
