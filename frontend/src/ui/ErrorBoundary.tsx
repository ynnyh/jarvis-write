// 全局错误边界:渲染崩溃时给出可恢复的界面,而不是白屏
import { Component, ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card notice-err" style={{ margin: 24 }}>
          <h2>页面出错了</h2>
          <div className="muted mt-2">{String(this.state.error.message || this.state.error)}</div>
          <div className="actions mt-3">
            <button className="primary" onClick={() => { this.setState({ error: null }); }}>
              重试
            </button>
            <button onClick={() => { window.location.hash = "#/"; window.location.reload(); }}>
              回首页
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
