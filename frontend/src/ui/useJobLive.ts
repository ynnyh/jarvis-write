// useJobLive:订阅某个后台任务的「实时正文」——模型正在写的字,逐字到浏览器。
// 后端一条 SSE 端点覆盖全部任务(见 backend/app/live.py),这里只管接帧、拼字、断线重连。
// 长任务动辄几十分钟,反代/CDN 会掐空闲连接,所以流意外结束(没收到 done)一律带 cursor 续订。
import { useEffect, useRef, useState } from "react";
import { ApiError, api, SseFrame } from "../api";

const MAX_CHARS = 6000;   // 只留尾部:直播看的是"现在写到哪",不是全文
const RETRY_MS = 2000;    // 断线重连间隔
const MAX_RETRIES = 30;   // 连不上就放弃(任务本身不受影响,轮询仍在)

export interface JobLiveState {
  /** 当前这一屏的正文(步骤切换会清屏) */
  text: string;
  /** 当前步骤(与任务中心的 stage 同源) */
  step: string;
  /** 流是否连着 */
  streaming: boolean;
  /** 已收到 done:任务结束,不再有新字 */
  ended: boolean;
}

const EMPTY: JobLiveState = { text: "", step: "", streaming: false, ended: false };

/** 订阅任务实时正文。jobId 为空或 enabled=false 时不连接。 */
export function useJobLive(jobId: string | null, enabled = true): JobLiveState {
  const [state, setState] = useState<JobLiveState>(EMPTY);
  // 存最新 cursor,重连时续看(不放 state,避免每帧触发重连)
  const cursorRef = useRef(0);

  useEffect(() => {
    setState(EMPTY);
    cursorRef.current = 0;
    if (!jobId || !enabled) return;

    const ctrl = new AbortController();
    let stopped = false;
    let retries = 0;

    const onFrame = (frame: SseFrame) => {
      const d = (frame.data ?? {}) as {
        text?: string; step?: string; seq?: number; status?: string;
      };
      if (typeof d.seq === "number") cursorRef.current = d.seq;
      switch (frame.event) {
        case "step":  // 换屏(首帧也走这):整屏替换
          setState((s) => ({ ...s, step: d.step ?? "", text: d.text ?? "", streaming: true }));
          break;
        case "label": // 同一步里的进度计数(如「已生成 3/40 章」):只换标签,正文照旧
          setState((s) => ({ ...s, step: d.step ?? "" }));
          break;
        case "reset": // 落后太多、服务端缓冲已滚过:整屏重置,不假装连续
          setState((s) => ({ ...s, text: d.text ?? "", streaming: true }));
          break;
        case "token":
          setState((s) => ({
            ...s, streaming: true, text: (s.text + (d.text ?? "")).slice(-MAX_CHARS),
          }));
          break;
        case "done":
          stopped = true;
          setState((s) => ({ ...s, streaming: false, ended: true }));
          break;
        default: // ping 等:只为保活,不改状态
          break;
      }
    };

    (async () => {
      while (!stopped) {
        try {
          setState((s) => ({ ...s, streaming: true }));
          await api.followJobLive(jobId, cursorRef.current, onFrame, ctrl.signal);
        } catch (e) {
          if (ctrl.signal.aborted) return;
          // 任务已被清理/登录态失效:重试无意义
          if (e instanceof ApiError && (e.status === 404 || e.status === 401)) {
            setState((s) => ({ ...s, streaming: false, ended: true }));
            return;
          }
          if (++retries > MAX_RETRIES) {
            setState((s) => ({ ...s, streaming: false }));
            return;
          }
        }
        if (stopped || ctrl.signal.aborted) return;
        // 流结束但没收到 done → 多为反代掐了空闲连接,歇一下带 cursor 续订
        setState((s) => ({ ...s, streaming: false }));
        await new Promise((r) => setTimeout(r, RETRY_MS));
      }
    })();

    return () => { stopped = true; ctrl.abort(); };
  }, [jobId, enabled]);

  return state;
}
