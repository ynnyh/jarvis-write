// 登录 / 注册页(阶段 8:多用户)。注册需邀请码。
import { useState } from "react";
import { api, token, Me } from "../api";

interface Props { onAuthed: (me: Me) => void; }

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = mode === "login"
        ? await api.login(username.trim(), password)
        : await api.register(username.trim(), password, invite.trim());
      token.set(r.token);
      onAuthed(await api.me());
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <h1 className="auth-brand">jarvis<span>·write</span></h1>
        <div className="auth-sub">AI 长篇小说工作台 · 从一句灵感到一部成书</div>

        <div className="tabs auth-tabs">
          <div className={"tab" + (mode === "login" ? " on" : "")} onClick={() => { setMode("login"); setErr(""); }}>登录</div>
          <div className={"tab" + (mode === "register" ? " on" : "")} onClick={() => { setMode("register"); setErr(""); }}>注册</div>
        </div>

        <form onSubmit={submit}>
          <label className="fl">用户名</label>
          <input type="text" value={username} autoComplete="username"
            onChange={(e) => setUsername(e.target.value)} placeholder="2-50 个字符" />

          <label className="fl">密码</label>
          <input type="password" value={password}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            onChange={(e) => setPassword(e.target.value)} placeholder="至少 6 位" />

          {mode === "register" && (
            <>
              <label className="fl">邀请码</label>
              <input type="text" value={invite}
                onChange={(e) => setInvite(e.target.value)} placeholder="向站长索取" />
            </>
          )}

          <button className="primary btn-lg btn-block" type="submit" disabled={busy}>
            {busy && <span className="spin" />}{mode === "login" ? "登录" : "注册并登录"}
          </button>
        </form>

        {err && <div className="notice notice-err">{err}</div>}
        <div className="auth-note">
          登录后请到「模型设置」配置你自己的模型 key。每个账号的 key 相互独立,不共用。
        </div>
      </div>
    </div>
  );
}
