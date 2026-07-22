// 全局任务中心:轮询 /api/jobs 汇总当前用户所有后台任务,
// 顶栏角标常驻可感知,抽屉看进度;任意页面发起的长任务都在这里可见。
import {
  createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState,
} from "react";
import { api } from "../api";

export interface BgJob {
  job_id: string;
  kind: string;
  status: "running" | "done" | "error";
  stage: string;
  error?: string | null;
}

interface TaskCenterValue {
  jobs: BgJob[];
  running: BgJob[];
  /** 发起长任务后调用,立刻把任务塞进列表并加速轮询 */
  track: (job: Pick<BgJob, "job_id" | "kind">) => void;
  refresh: () => void;
}

const Ctx = createContext<TaskCenterValue>({
  jobs: [], running: [], track: () => {}, refresh: () => {},
});

export const useTaskCenter = () => useContext(Ctx);

/** kind → 人话标签(chapter-3-5 → 「第 5 章生成」) */
export function jobLabel(kind: string): string {
  if (/^chapter-\d+-queue$/.test(kind)) return "连写队列";
  let m = kind.match(/^chapter-\d+-(\d+)$/);
  if (m) return `第 ${m[1]} 章生成`;
  m = kind.match(/^re-extract-\d+-(\d+)$/);
  if (m) return `第 ${m[1]} 章一致性同步`;
  m = kind.match(/^polish-\d+-(\d+)$/);
  if (m) return `第 ${m[1]} 章润色`;
  if (kind.startsWith("polish-segment")) return "选段润色";
  if (kind.startsWith("architecture-")) return "架构生成";
  if (kind.startsWith("blueprint-")) return "蓝图生成";
  m = kind.match(/^impact-\d+-(\d+)$/);
  if (m) return `第 ${m[1]} 章影响分析`;
  if (kind.startsWith("cascade-")) return "级联重生成";
  if (kind.startsWith("synopsis-")) return "简介生成";
  if (kind.startsWith("inspire-refine")) return "概念改写";
  if (kind.startsWith("inspire")) return "灵感方案";
  return kind;
}

const IDLE_MS = 15000;   // 无任务时慢轮询
const ACTIVE_MS = 3000;  // 有任务在跑时快轮询

export function TaskCenterProvider({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const [jobs, setJobs] = useState<BgJob[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  const jobsRef = useRef<BgJob[]>([]);
  jobsRef.current = jobs;

  const poll = useCallback(async () => {
    try {
      const r = await api.myJobs(true);
      // 保序合并:接口是全量,直接替换;done/error 状态保留展示(后端 200 条内不清 running)
      setJobs(r.jobs as BgJob[]);
    } catch {
      // 轮询失败静默,下轮再试(未登录/网络抖动都会走到这)
    }
  }, []);

  useEffect(() => {
    if (!enabled) { setJobs([]); return; }
    let stopped = false;
    const loop = async () => {
      if (stopped) return;
      await poll();
      const anyRunning = jobsRef.current.some((j) => j.status === "running");
      timerRef.current = setTimeout(loop, anyRunning ? ACTIVE_MS : IDLE_MS);
    };
    loop();
    return () => { stopped = true; clearTimeout(timerRef.current); };
  }, [enabled, poll]);

  const track = useCallback((job: Pick<BgJob, "job_id" | "kind">) => {
    setJobs((js) =>
      js.some((j) => j.job_id === job.job_id)
        ? js
        : [...js, { ...job, status: "running", stage: "排队中" }],
    );
    // 加速下一轮:立刻拉一次真实状态
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      await poll();
      timerRef.current = setTimeout(async function loop() {
        await poll();
        const anyRunning = jobsRef.current.some((j) => j.status === "running");
        timerRef.current = setTimeout(loop, anyRunning ? ACTIVE_MS : IDLE_MS);
      }, ACTIVE_MS);
    }, 500);
  }, [poll]);

  const running = jobs.filter((j) => j.status === "running");
  return (
    <Ctx.Provider value={{ jobs, running, track, refresh: poll }}>
      {children}
    </Ctx.Provider>
  );
}

/** 顶栏角标 + 下拉任务抽屉 */
export function TaskCenterBadge() {
  const { jobs, running } = useTaskCenter();
  const [open, setOpen] = useState(false);
  const recent = [...jobs].reverse().slice(0, 12);
  if (!jobs.length) return null;
  return (
    <div className="tc-wrap">
      <button
        className={"tc-badge" + (running.length ? " busy" : "")}
        onClick={() => setOpen(!open)}
        title="后台任务"
      >
        {running.length ? <span className="spin" /> : "✓"}
        <span>{running.length ? `${running.length} 个任务` : "任务"}</span>
      </button>
      {open && (
        <div className="tc-drawer" onMouseLeave={() => setOpen(false)}>
          {recent.map((j) => (
            <div key={j.job_id} className={"tc-item " + j.status}>
              <span className="tc-label">{jobLabel(j.kind)}</span>
              <span className="tc-stage">
                {j.status === "running" && <span className="spin" />}
                {j.status === "error" ? (j.error || "失败") : j.stage}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
