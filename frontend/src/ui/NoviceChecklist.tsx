// NoviceChecklist — 新手轨三步清单(docs/19 中期「分期 reveal」的正面部分):
// 定核心梗卡 → 写完第 1 章 → 完成首次验收(通过审核),三步走完自动消失。
// 为什么只有三步:留存决胜负在前 30 分钟;专业能力(级联/连写/制片线)靠
// 分期 reveal 渐进呈现,而不是一开始铺满术语墙。
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { qk, useChapters } from "../hooks/queries";
import { api } from "../api";

const DONE_KEY = "novice-checklist-done";

export default function NoviceChecklist({ pid, onGoto }: {
  pid: number;
  onGoto: (path: string) => void;
}) {
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(
    () => { try { return localStorage.getItem(`${DONE_KEY}:${pid}`) === "1"; } catch { return false; } },
  );
  // 三步信号:梗卡(接口)/ 首章(章节列表)/ 首次验收(任一 approved 章)
  const premise = useQuery({ queryKey: qk.premise(pid), queryFn: () => api.getPremise(pid) });
  const chapters = useChapters(pid);

  if (dismissed) return null;
  const hasPremise = !!premise.data;
  const hasFirst = (chapters.data ?? []).some((c: { word_count: number }) => c.word_count > 0);
  const hasApproved = (chapters.data ?? []).some((c: { status: string }) => c.status === "approved");
  const allDone = hasPremise && hasFirst && hasApproved;
  // 全部完成自动消失;「知道了」手动收起(本书记忆,不复现)
  if (allDone) {
    try { localStorage.setItem(`${DONE_KEY}:${pid}`, "1"); } catch { /* 隐私模式忽略 */ }
    return null;
  }
  if (premise.isLoading || chapters.isLoading) return null;

  function dismiss() {
    try { localStorage.setItem(`${DONE_KEY}:${pid}`, "1"); } catch { /* 隐私模式忽略 */ }
    setDismissed(true);
  }

  const steps = [
    {
      done: hasPremise, label: "定核心梗卡",
      desc: "梗是全书的纲:蓝图、对账、体检都以它为轴",
      path: `/project/${pid}/settings`,
    },
    {
      done: hasFirst, label: "写完第 1 章",
      desc: "选一章,点「让 AI 写」,蓝图前情自动备齐",
      path: `/project/${pid}/write?ch=1`,
    },
    {
      done: hasApproved, label: "完成首次验收",
      desc: "看交稿单:没问题点「通过审核」,有问题按建议改",
      path: `/project/${pid}/write?ch=1`,
    },
  ];

  return (
    <div className="novice-checklist" data-testid="novice-checklist">
      <div className="novice-head">
        <b>🧭 前三步走完,这本书就上轨道了</b>
        <span className="grow" />
        <button className="btn-sm" title="收起(本书记忆)" onClick={dismiss}>知道了</button>
      </div>
      <ol className="novice-steps">
        {steps.map((s, i) => (
          <li key={s.label} className={s.done ? "done" : ""}>
            <span className="novice-step-no">{s.done ? "✓" : i + 1}</span>
            <span className="novice-step-body">
              <b>{s.label}</b>
              <span className="muted">{s.desc}</span>
            </span>
            {!s.done && (
              <button className="btn-sm" onClick={() => { onGoto(s.path); navigate(s.path); }}>去做</button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
