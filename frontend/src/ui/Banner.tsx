// 长任务进行中的横幅:转圈 + 当前阶段文案(stage 由 ui/useJob 的 onStage 喂进来)。
// 三条出片线都要这一条,别再各写一份——漫剧与宣传片此前各有一个同名本地组件,
// 一模一样的 12 个字符 JSX,改一处忘一处。
export default function Banner({ stage, text }: { stage: string; text: string }) {
  return (
    <div className="gen-banner">
      <span className="spin" />
      <span className="gen-banner-text">{stage || text}</span>
    </div>
  );
}
