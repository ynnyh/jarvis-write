// 应用锁锁屏页(仅桌面单机 local 模式且已设锁才会出现,见 App.tsx 的启动引导)。
// 全屏密码输入,解锁成功才渲染主界面;Enter 提交,错误就地提示。
// 威胁模型:防家人/同事的休闲锁,不抵御本地数据被直接读取;
// 解锁标记存 sessionStorage——刷新同标签页不重输,关掉重开要重输。
import { useState } from "react";
import { api } from "../api";

interface Props { onUnlocked: () => void; }

export default function LockScreen({ onUnlocked }: Props) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // 忘记密码:就地展开重置区块(说明 + 输入「重置」二字确认)
  const [showReset, setShowReset] = useState(false);
  const [resetConfirm, setResetConfirm] = useState("");
  const [resetErr, setResetErr] = useState("");
  const [resetBusy, setResetBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password || busy) return;
    setErr(""); setBusy(true);
    try {
      await api.appLockUnlock(password);
      onUnlocked();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  async function doReset() {
    if (resetConfirm !== "重置" || resetBusy) return;
    setResetErr(""); setResetBusy(true);
    try {
      await api.appLockReset(resetConfirm);
      // 锁已移除:直接视为解锁进入,体验最顺(数据未动,无需再确认)
      onUnlocked();
    } catch (e) {
      setResetErr(e instanceof Error ? e.message : String(e));
    } finally {
      setResetBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <h1 className="auth-brand">jarvis<span>·write</span></h1>
        <div className="auth-sub">已开启应用锁,输入密码进入</div>

        <form onSubmit={submit}>
          <label className="fl">应用锁密码</label>
          <input
            type="password"
            value={password}
            autoFocus
            onChange={(e) => setPassword(e.target.value)}
            placeholder="输入应用锁密码"
          />
          <button className="primary btn-lg btn-block" type="submit" disabled={busy || !password}>
            {busy && <span className="spin" />}解锁
          </button>
        </form>

        {err && <div className="notice notice-err">{err}</div>}

        {!showReset && (
          <div className="auth-note">
            <a
              className="linkbtn"
              onClick={() => { setShowReset(true); setErr(""); }}
            >
              忘记密码?
            </a>
          </div>
        )}

        {showReset && (
          <div className="auth-note">
            <p>
              重置将移除应用锁,你的书籍和设置数据不受影响;
              重置后进入可在「设置 → 应用锁」重新设置。
            </p>
            <label className="fl">输入「重置」二字确认</label>
            <input
              type="text"
              value={resetConfirm}
              onChange={(e) => setResetConfirm(e.target.value)}
              placeholder="重置"
            />
            {resetErr && <div className="notice notice-err">{resetErr}</div>}
            <div className="provider-actions">
              <button
                type="button"
                className="btn-sm danger"
                disabled={resetConfirm !== "重置" || resetBusy}
                onClick={doReset}
              >
                {resetBusy && <span className="spin" />}确认重置
              </button>
              <button
                type="button"
                className="btn-sm"
                onClick={() => { setShowReset(false); setResetConfirm(""); setResetErr(""); }}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
