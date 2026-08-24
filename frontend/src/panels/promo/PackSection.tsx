// ⑥-2 成片包与导出(主区):配音稿(解说逐镜对位 + 估时)+ 剪辑清单(转场/配乐)+ 四种导出。
// pack 在后端是 JSON 自由字段,所以这里显式声明一份读法,别让 unknown 漏到 JSX 里。
import { PromoPlan, promoApi } from "../../promoApi";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { PromoJobs } from "./usePromoJobs";

interface PromoPack {
  narration_full?: string;
  dubbing?: { seq: number; text: string; est_s: number; shot_duration_s: number }[];
  checklist?: {
    seq: number; scene: string; duration_s: number;
    subtitle: string; transition: string; bgm_tag: string; note: string;
  }[];
  totals?: { shots: number; storyboard_s: number; target_s: number; voice_s: number };
}

export default function PackSection({ pid, plan, shotCount, jobs, onBuild }: {
  pid: number;
  plan: PromoPlan;
  shotCount: number;
  jobs: PromoJobs;
  onBuild: () => void;
}) {
  const pack = (plan.pack ?? {}) as PromoPack;
  const checklist = pack.checklist ?? [];

  async function exp(fmt: "md" | "csv" | "srt" | "json") {
    try { await promoApi.export(pid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">⑥-2 成片包与导出 <span className="muted">配音稿 + 剪辑清单</span></h3>
        <button className="primary" disabled={!!jobs.busy || shotCount === 0} onClick={onBuild}>
          {checklist.length ? "重建成片包" : "出成片包"}
        </button>
      </div>
      {jobs.busy === "pack" && <Banner stage={jobs.stage} text="AI 正在出成片包…" />}

      <div className="card-head mb-2">
        <span className="muted">导出:</span>
        <button className="btn-sm" onClick={() => exp("md")}>拍摄手册</button>
        <button className="btn-sm" disabled={shotCount === 0} onClick={() => exp("csv")}>分镜CSV</button>
        <button className="btn-sm" disabled={shotCount === 0} onClick={() => exp("srt")}>字幕SRT</button>
        <button className="btn-sm" onClick={() => exp("json")}>JSON</button>
      </div>

      {checklist.length === 0 ? (
        <p className="hint">
          出成片包:解说逐镜对位并估时(念不完的会标出来),再给一份带转场与配乐标注的剪辑清单。
        </p>
      ) : (
        <>
          <div className="card-head mb-2">
            <b>成片包</b>
            <span className="muted">
              {pack.totals?.shots} 格 · 分镜 {pack.totals?.storyboard_s}s(目标 {pack.totals?.target_s}s)
              · 解说估时 {pack.totals?.voice_s}s
            </span>
          </div>
          {pack.narration_full && (
            <div className="sub-summary">
              <div className="card-head mb-2">
                <b>整段解说(粘给 TTS)</b>
                <span className="grow" />
                <CopyBtn text={pack.narration_full} />
              </div>
              <div className="script-line pre-wrap">{pack.narration_full}</div>
            </div>
          )}
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>#</th><th>场景</th><th>秒</th><th>字幕</th><th>转场</th><th>配乐</th><th>备注</th></tr>
              </thead>
              <tbody>
                {checklist.map((c) => (
                  <tr key={c.seq}>
                    <td>{c.seq}</td><td>{c.scene}</td><td>{c.duration_s}s</td><td>{c.subtitle}</td>
                    <td>{c.transition}</td><td>{c.bgm_tag}</td><td className="muted">{c.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
