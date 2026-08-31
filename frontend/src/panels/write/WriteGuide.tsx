// WriteGuide — 写作区首次进入的 3 步引导条(P0 认知减负,只此一次)。
// 新用户面对写作区最大的困惑是「什么时候该管什么」:目录在哪、怎么让 AI 写、
// 写完要不要管。这里用一条窄横幅把主干动线讲完,点「知道了」永久收起
// (localStorage 记住,双端各自一次,不打扰老用户)。不放插画不放多列,
// 一行三步 + 关闭,读完 5 秒内的事。
import { useState } from "react";

const GUIDE_KEY = "jarvis-write-guide-v1";

function dismissed(): boolean {
  try { return localStorage.getItem(GUIDE_KEY) === "1"; } catch { return false; }
}

const STEPS: { title: string; desc: string }[] = [
  {
    title: "① 选一章",
    desc: "点上方章题打开目录,已写的直接读,没写的随时让 AI 写",
  },
  {
    title: "② 让 AI 写",
    desc: "蓝图、前情、人物状态会自动备齐,你只管点「生成」",
  },
  {
    title: "③ 过目把关",
    desc: "写完看一眼结果卡:有问题按建议改,没问题直接通过",
  },
];

export default function WriteGuide() {
  const [hidden, setHidden] = useState(dismissed);
  if (hidden) return null;

  function dismiss() {
    try { localStorage.setItem(GUIDE_KEY, "1"); } catch { /* 隐私模式下忽略 */ }
    setHidden(true);
  }

  return (
    <div className="write-guide" role="note">
      <div className="write-guide-steps">
        {STEPS.map((s) => (
          <div className="write-guide-step" key={s.title}>
            <b>{s.title}</b>
            <span>{s.desc}</span>
          </div>
        ))}
      </div>
      <button className="btn-sm" onClick={dismiss}>知道了</button>
    </div>
  );
}
