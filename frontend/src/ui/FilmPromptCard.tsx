// src/ui/FilmPromptCard.tsx — 各工坊共用的「整片提示词」卡片。
// 一份实现四处用(漫剧/情绪·灵感·故事/宣传片):生成走各自工坊的 job,
// 文本框可手改、可整段粘贴自己写的版本(保存即替换),CopyBtn 一键复制。
import { useEffect, useState } from "react";

import { errMsg } from "../pollJob";
import { CopyBtn } from "./copy";
import { toast } from "./Toaster";
import { useJob } from "./useJob";

export function FilmPromptCard({
  load,
  save,
  generate,
  jobKind,
  ready = true,
  readyHint = "先完成分镜,才有原料组装整片提示词",
  generateDetail = "文本框可直接改;一键复制贴去 Sora/Veo/可灵一次出一整片",
}: {
  /** 读当前稿(空串 = 还没生成过) */
  load: () => Promise<string>;
  /** 整段替换保存,返回落库后的稿 */
  save: (text: string) => Promise<string>;
  /** 发起 AI 生成 job */
  generate: () => Promise<{ job_id: string }>;
  /** 任务中心的 kind 标识(同对象去重由后端负责) */
  jobKind: string;
  /** 前置原料是否就绪(有分镜/已选本子);false 时禁用生成并给提示 */
  ready?: boolean;
  readyHint?: string;
  generateDetail?: string;
}) {
  const { run } = useJob();
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    load()
      .then((t) => { if (alive) { setText(t); setDirty(false); } })
      .catch(() => { /* 读取失败按空稿处理,不影响页面 */ })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobKind]);

  async function generateNow() {
    setBusy(true); setErr("");
    try {
      await run(generate, { kind: jobKind });
      setText(await load());
      setDirty(false);
      toast.ok("整片提示词已生成", generateDetail);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  }

  async function saveNow() {
    try {
      setText(await save(text));
      setDirty(false);
      toast.ok("整片提示词已保存");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  return (
    <div className="card">
      <div className="card-head mb-2">
        <b>整片提示词(端到端模型)</b>
        <span className="badge">Sora / Veo / 可灵</span>
        <span className="grow" />
        {text && !loading && (
          <CopyBtn text={text} label="一键复制" title="整段复制,贴进端到端视频模型直接生成" />
        )}
        <button className="btn-sm" disabled={!dirty || busy} onClick={() => void saveNow()}>
          {dirty ? "保存修改" : "已保存"}
        </button>
        <button className="primary" disabled={busy || loading || !ready}
          title={ready ? (text ? "按最新分镜重新组装(覆盖现有内容)" : "按分镜+设定组装整片提示词") : readyHint}
          onClick={() => void generateNow()}>
          {busy ? "生成中…" : text ? "重新生成" : "AI 生成整片提示词"}
        </button>
      </div>
      <p className="hint">
        把每个镜头怎么拍、人物长什么样、环境什么氛围综合成一条完整提示词,贴出去一次生成整片;
        也可以把自己写好的版本整段粘贴进来保存。改了内容后点「重新生成」即可同步。
      </p>
      {err && <div className="msg-err">{err}</div>}
      <textarea
        rows={Math.min(16, Math.max(6, text.split("\n").length + 1))}
        value={text}
        placeholder={loading ? "加载中…"
          : !ready ? readyHint
          : "还没有整片提示词:点「AI 生成」按分镜组装,或把自己写的整段粘贴进来。"}
        disabled={loading}
        onChange={(e) => { setText(e.target.value); setDirty(true); }}
      />
    </div>
  );
}
