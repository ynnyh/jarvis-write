// EmptyState — 统一空态容器(虚线框居中),替代各处手写的「card muted」空提示
export default function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
