import { useCallback, useEffect, useState } from "react";
import type { ClipPlan } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import { errMsg } from "../../pollJob";
import { PasteBox } from "./PasteBox";
import { CLIP_LIMIT_KEY, VIDEO_PLATFORM_KEY } from "./dramaShared";

/** 视频段计划:治「视频站一次最多 5-15 秒,分镜格却是 2-8 秒」。
 *
 *  真实做法是「一段生成一次,再在画布/剪映里首尾相接」,所以这里把相邻的格
 *  按四条规则并成段(同场景 / 不引入新角色 / 不超上限 / 最多一条台词),
 *  每段给首帧是哪一格、怎么动、几秒、要压什么字幕。上限换档即时重算(纯确定性)。
 */
export function ClipPlanSection({ pid, eid, sig }: { pid: number; eid: number; sig: string }) {
  const [limit, setLimit] = useState(() => Number(localStorage.getItem(CLIP_LIMIT_KEY)) || 10);
  const [plan, setPlan] = useState<ClipPlan | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setPlan((await dramaApi.getClips(pid, eid, limit)).plan);
      setErr("");
    } catch (e) { setErr(errMsg(e)); }
  }, [pid, eid, limit]);

  // sig 变了 = 分镜/运动轨改过,段计划跟着重算(时长一改,并段结果就不一样了)
  useEffect(() => { void load(); }, [load, sig]);

  function pickLimit(n: number) {
    setLimit(n);
    localStorage.setItem(CLIP_LIMIT_KEY, String(n));
  }

  const options = plan?.options?.length ? plan.options : [5, 10, 15];
  const runs = plan ? plan.totals.segments + plan.totals.extra_runs : 0;

  return (
    <>
      <div className="card-head mb-2">
        <b>让它动起来:视频段计划</b>
        <span className="muted">你那家视频站单次最多能出几秒?</span>
        {options.map((n) => (
          <button key={n} className={"chip" + (limit === n ? " on" : "")}
            onClick={() => pickLimit(n)}>{n} 秒</button>
        ))}
      </div>
      {err && <div className="msg-err">{err}</div>}
      <p className="hint">
        视频站单次只能出 {limit} 秒,而分镜格是 2-8 秒——所以按「一段生成一次」并好段,
        你照段号顺序在画布/剪映里首尾相接就是成片。首帧图用该段第一格出好的静帧,
        人物长相全靠它锁住;提示词里<b>刻意不写外貌</b>(写了模型会重画脸)。
      </p>
      {plan && (
        <>
          <p className="hint wb-next">
            共 <b>{plan.totals.segments}</b> 段 · 合计 <b>{plan.totals.duration_s}</b> 秒 ·
            要生成 <b>{runs}</b> 次 · 首帧图已就位{" "}
            <b>{plan.totals.first_frames_ready}/{plan.totals.segments}</b> 段
            {plan.totals.over_limit > 0
              ? ` · ⚠ 其中 ${plan.totals.over_limit} 段单格就超过 ${limit} 秒,要靠尾帧续接`
              : " · 每段一次出得完"}
          </p>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>段</th><th>含分镜</th><th>场景</th><th>角色</th><th>秒</th><th>生成次数</th><th>首帧</th><th>首帧图</th><th>这一段怎么动</th></tr>
              </thead>
              <tbody>
                {plan.segments.map((seg) => (
                  <tr key={seg.index} className={seg.over_limit ? "row-warn" : ""}>
                    <td>{seg.index}</td>
                    <td>{seg.seqs.join("、")}</td>
                    <td>{seg.scene_name}</td>
                    <td>{seg.characters.join("、") || "(空镜)"}</td>
                    <td>{seg.duration_s}</td>
                    <td>{seg.runs}</td>
                    <td>{seg.first_frame}</td>
                    {/* 这一段能不能开工全看它:段首格的静帧挂上来了才有首帧图可传 */}
                    <td>{seg.first_frame_ready ? "✓ 已挂" : "待出图"}</td>
                    <td>{seg.motion}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {plan.segments.map((seg) => (
            <div key={seg.index} className="sub-summary">
              <div className="card-head mb-2">
                <b>视频段 {seg.index}(分镜 {seg.seqs.join("、")} · {seg.duration_s}s)</b>
                <span className="muted">首帧:{seg.first_frame}</span>
                <span className="badge">{seg.first_frame_ready ? "首帧图已就位" : "首帧图还没挂"}</span>
                {seg.over_limit && <span className="badge">要生成 {seg.runs} 次</span>}
              </div>
              {seg.split_hint && <div className="notice notice-warn">{seg.split_hint}</div>}
              {!seg.first_frame_ready && (
                <p className="hint">
                  先把{seg.first_frame}出好、在上面那一格「挂静帧」挂回来,再拿这段提示词去视频站——
                  没有首帧图就只能走文生视频,人物每段一张脸。
                </p>
              )}
              <PasteBox paste={seg.paste} rows={7}
                storeKey={VIDEO_PLATFORM_KEY} title="一键粘贴 · 你用的视频站" />
              {seg.dialogue && <p className="hint">这一段的字幕/配音:{seg.dialogue}</p>}
            </div>
          ))}
          <p className="hint">{plan.note}</p>
        </>
      )}
    </>
  );
}
