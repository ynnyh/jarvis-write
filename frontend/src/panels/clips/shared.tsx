// 情绪短片/灵感工坊共用件:切段聚合、导向 chips、元数据(命题/画风/导向)一次拉一份。
// 为什么 clips 线不套 wb-cols 双栏:宣传片/漫剧是「锚资产常驻侧栏 + 推进主区」的并行轨
// 工作台;clips 是串行的「出本子→定手卡→出片」,没有要随时回头看的锚资产,强拆双栏反而
// 多一层空侧栏。统一的是「现在该点哪个按钮」的步骤条心智——与另两条出片线共用 StepBar。
import { useEffect, useState } from "react";
import { ClipShot, ClipTheme, ClipDirection, SteeringOption, clipsApi } from "../../clipsApi";

export interface ClipsMeta {
  themes: ClipTheme[];
  plays: ClipTheme[];
  durations: number[];
  directions: ClipDirection[];
  dialogue_styles: SteeringOption[];
  pacings: SteeringOption[];
  intensities: SteeringOption[];
}

export function useClipsMeta() {
  const [meta, setMeta] = useState<ClipsMeta | null>(null);
  useEffect(() => { void clipsApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

/** 企划状态 → 徽章色调:待生成(warn) / 候选已出(中性 brand,默认) / 已选定(ok)。
 * 让列表与工作台里的状态徽章能「读色」,而不是一屏同一种蓝。 */
export function clipStatusTone(status: string): "" | "ok" | "warn" {
  if (status === "draft") return "warn";
  if (status === "picked") return "ok";
  return "";
}

// 按切段聚合该段镜头的中文提示词:minimax H3 这类图文生视频是一段一生成,
// 要的是"这一段直接贴进去"的一整段;散在分镜表里的逐格词得自己拼,那不算一键。
export function chunkPromptText(shots: ClipShot[], seqs: number[]): string {
  const set = new Set(seqs);
  return shots
    .filter((s) => set.has(s.seq))
    .map((s) => `镜头${s.seq}(${s.duration_s}s): ${s.prompt_cn}`)
    .join("\n\n");
}

/** 导向维度 chips 组(auto=AI 定;目录由后端下发,前端只渲染) */
export function SteeringChips({ label, options, value, onChange }: {
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