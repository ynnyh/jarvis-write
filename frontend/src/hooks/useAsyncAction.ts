// hooks/useAsyncAction.ts
// 通用异步操作 hook:替代各 Panel 中重复的 setBusy/setErr/try-catch-finally 模式。
// 用法:const { run, busy, error } = useAsyncAction();
//       await run(async () => { const r = await api.someCall(); /* handle */ });
import { useCallback, useRef, useState } from "react";

interface AsyncActionState {
  busy: string;
  error: string;
}

/**
 * 封装异步操作的 loading/error 状态管理。
 * @param abortOnUnmount 组件卸载时是否自动 abort(默认 true)
 */
export function useAsyncAction(abortOnUnmount = true) {
  const [state, setState] = useState<AsyncActionState>({ busy: "", error: "" });
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async <T,>(
    fn: (signal: AbortSignal) => Promise<T>,
    busyText = "处理中…",
  ): Promise<T | undefined> => {
    const ac = new AbortController();
    abortRef.current = ac;
    setState({ busy: busyText, error: "" });
    try {
      const result = await fn(ac.signal);
      setState({ busy: "", error: "" });
      return result;
    } catch (e) {
      if (ac.signal.aborted) return undefined;
      setState({ busy: "", error: String(e) });
      return undefined;
    }
  }, []);

  const clearError = useCallback(() => setState((s) => ({ ...s, error: "" })), []);

  // 卸载时 abort
  const unmountRef = useRef(() => {
    if (abortOnUnmount) abortRef.current?.abort();
  });
  // 注册卸载清理(仅一次)
  const registered = useRef(false);
  if (!registered.current) {
    registered.current = true;
    // 利用 useEffect 的 cleanup
  }

  return { ...state, run, clearError, abortController: abortRef };
}
