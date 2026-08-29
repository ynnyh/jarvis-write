// LiveDock:全局「实时正文」窗——后台任务里模型正在写的字,在这里逐字滚出来。
//
// 为什么做成全局悬浮窗而不是逐面板内嵌:后端有 20+ 处建任务点、60+ 处 LLM 调用,
// 逐个面板加直播必漏;而任务中心已经汇总了所有任务,这里跟着"当前在跑的那个任务"
// 订阅即可,天然覆盖全链路(含以后新增的任务),也不用改各面板。
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { errMsg } from "../pollJob";
import { confirmDialog } from "./ConfirmDialog";
import { jobLabel, useTaskCenter } from "./TaskCenter";
import { toast } from "./Toaster";
import { useJobLive } from "./useJobLive";

const OPEN_KEY = "jarvis_live_dock_open";

export function LiveDock() {
  const { jobs, running, liveJobId, focusLive } = useTaskCenter();
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== "0");
  const bodyRef = useRef<HTMLDivElement>(null);
  // 用户手动往上翻时不再抢滚动条,翻回底部自动恢复跟随
  const pinnedRef = useRef(true);

  // 盯哪个任务:优先用户点选的(且仍在跑),否则最新在跑的那个
  const target = useMemo(() => {
    const picked = liveJobId ? running.find((j) => j.job_id === liveJobId) : undefined;
    return picked ?? running[running.length - 1] ?? null;
  }, [liveJobId, running]);

  const { text, step, streaming, ended } = useJobLive(target?.job_id ?? null, open);

  useEffect(() => {
    localStorage.setItem(OPEN_KEY, open ? "1" : "0");
  }, [open]);

  // 选中的任务跑完了 → 让选择回到"自动跟最新"
  useEffect(() => {
    if (liveJobId && !running.some((j) => j.job_id === liveJobId)) focusLive(null);
  }, [liveJobId, running, focusLive]);

  useEffect(() => {
    const el = bodyRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [text]);

  // 手动终止正在盯的任务:掐断进行中的模型调用,省下后续 token
  const [stopping, setStopping] = useState(false);
  async function stopTarget() {
    if (!target) return;
    const ok = await confirmDialog({
      title: "终止这个任务?",
      body: "进行中的模型调用会立刻掐断;已消耗的 token 不退,半成品不会保存。连写队列会整条停止。",
      confirmText: "终止",
      danger: true,
    });
    if (!ok) return;
    setStopping(true);
    try {
      await api.cancelJob(target.job_id);
      toast.ok("已请求终止", "任务正在停止");
    } catch (e) {
      toast.err("终止失败", errMsg(e));
    } finally {
      setStopping(false);
    }
  }

  if (!target) return null;

  const idx = running.findIndex((j) => j.job_id === target.job_id);
  const jobStage = jobs.find((j) => j.job_id === target.job_id)?.stage ?? "";

  return (
    <div className={"live-dock" + (open ? "" : " collapsed")}>
      <div className="live-head">
        <span className={"live-dot" + (streaming ? " on" : "")} />
        <span className="live-title" title={target.kind}>{jobLabel(target.kind)}</span>
        <span className="live-step" title={step || jobStage}>{step || jobStage}</span>
        <div className="grow" />
        {running.length > 1 && (
          <>
            <span className="live-nth">{idx + 1}/{running.length}</span>
            <button
              className="live-btn"
              title="看上一个任务"
              onClick={() => focusLive(running[(idx - 1 + running.length) % running.length].job_id)}
            >‹</button>
            <button
              className="live-btn"
              title="看下一个任务"
              onClick={() => focusLive(running[(idx + 1) % running.length].job_id)}
            >›</button>
          </>
        )}
        <button
          className="live-btn"
          title="终止这个任务(立刻掐断模型调用)"
          disabled={stopping}
          onClick={stopTarget}
        >■</button>
        <button
          className="live-btn"
          title={open ? "收起(不再接收实时正文)" : "展开看模型正在写什么"}
          onClick={() => setOpen(!open)}
        >
          {open ? "▾" : "▴"}
        </button>
      </div>
      {open && (
        <div
          className="live-body"
          ref={bodyRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}
        >
          {text ? (
            <>
              {text}
              {streaming && !ended && <span className="live-caret" />}
            </>
          ) : (
            <span className="muted">
              {ended
                ? "这一步没有流式正文(或任务刚结束)"
                : "模型还没开口——可能在读上下文/思考,吐字后这里会逐字滚动"}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
