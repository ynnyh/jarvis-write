// 工作台步骤条:✓ 已完成 / ▸ 现在做这步 / 序号 未开始,外加一行「现在做这步」指针。
// 漫剧与宣传片共用一份:两边都是一条线性管线,用户随时要知道「下一步点哪个按钮」——
// 只靠一屏屏卡片自己去猜,是这两个工坊最早的可用性问题。
//
// 点按滚到对应区:每个区的外层挂 id={`${anchorPrefix}-${step.key}`}。
// 语义用 <nav> 而不是 role="tablist":这些按钮是「跳到那一段」的锚点导航,不是页签
//(tablist 要求子项 role="tab" 且真的切换面板,读屏会读错)。
import { ReactNode } from "react";
import { toast } from "./Toaster";

export type Step = {
  key: string;
  label: string;
  done: boolean;
  /** 这一步到底该干什么的人话——只在它是「当前步」时显示,所以可以写长一点 */
  todo: string;
};

export default function StepBar({ steps, anchorPrefix, allDone }: {
  steps: Step[];
  anchorPrefix: string;
  /** 全部走完时替换指针行的话(各工坊自己说「接下来拿它去干什么」) */
  allDone: ReactNode;
}) {
  // 第一个未完成的就是「现在做这步」;全绿则为 null
  const current = steps.find((s) => !s.done) ?? null;
  const currentIndex = steps.findIndex((s) => !s.done); // 全绿为 -1

  function jump(key: string) {
    document.getElementById(`${anchorPrefix}-${key}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // 卡点引导:当前步与已完成步照常跳到对应区;「还没排到的未来步」与其静默滚到空区,
  // 不如明说缺哪一步、并把人直接带到当前该做的那步。漫剧/宣传片/clips 三线共用这套,
  // 用户不会在「点了没反应的步骤条」上猜下一步。
  function handleStep(s: Step, i: number) {
    const idx = currentIndex;
    if (i === idx || s.done || idx === -1) { jump(s.key); return; }
    toast.info(`先做「${steps[idx].label}」`, `${s.label} 还没轮到——等它前面的步就绪再回来。已帮你跳到当前步。`);
    jump(steps[idx].key);
  }

  return (
    <>
      <nav className="chips wb-steps" aria-label="工作台步骤">
        {steps.map((s, i) => (
          <button key={s.key} type="button"
            className={"chip" + (s.done ? " done" : "") + (current?.key === s.key ? " on" : "")}
            aria-current={current?.key === s.key}
            onClick={() => handleStep(s, i)}>
            {s.done ? "✓ " : current?.key === s.key ? "▸ " : `${i + 1} `}{s.label}
          </button>
        ))}
      </nav>
      {current ? (
        <p className="hint wb-next">
          <b>现在做这步:{current.label}</b> —— {current.todo}
          <button type="button" className="btn-sm" onClick={() => jump(current.key)}>去这一步</button>
        </p>
      ) : (
        <p className="hint wb-next">{allDone}</p>
      )}
    </>
  );
}
