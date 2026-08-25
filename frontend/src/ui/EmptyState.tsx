// EmptyState — 统一空态容器(虚线框居中),替代各处手写的「card muted」空提示。
// 自带一枚「场记板 + 星光」的轻插画,给空态一点品牌记忆点;纯装饰 SVG,不着重讲内容。
export default function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty-state">
      <svg className="empty-ico" viewBox="0 0 72 48" aria-hidden="true">
        {/* 场记板上板(平行四边形) */}
        <path d="M8 14 L64 10 L66 30 L10 34 Z"
          fill="var(--brand-weak)" stroke="var(--brand)" strokeWidth="2" strokeLinejoin="round" />
        {/* 上板斜条纹 */}
        <path d="M14 19 L60 15.5" stroke="var(--brand)" strokeWidth="1.6" strokeLinecap="round" />
        <path d="M15 24 L61 20.5" stroke="var(--brand)" strokeWidth="1.6" strokeLinecap="round" />
        {/* 下板 */}
        <path d="M20 39 L58 35.5 L60 41.5 L22 45 Z"
          fill="var(--surface)" stroke="var(--brand)" strokeWidth="2" strokeLinejoin="round" />
        {/* 右上星光 */}
        <path d="M68 8 l1.6 3.4 3.6 0.6 -2.6 2.6 0.6 3.6 -3.2 -1.8 -3.2 1.8 0.6 -3.6 -2.6 -2.6 3.6 -0.6 Z"
          fill="var(--brand)" />
      </svg>
      <div>{children}</div>
    </div>
  );
}