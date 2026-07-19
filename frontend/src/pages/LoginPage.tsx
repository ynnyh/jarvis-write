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
      onAuthed({ id: 0, username: r.username, is_admin: r.is_admin });
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <h1 style={{ marginBottom: 4 }}>jarvis<span style={{ color: "var(--brand)" }}>·write</span></h1>
        <div className="muted" style={{ marginBottom: 18 }}>AI 长篇小说工作台</div>

        <div className="tabs" style={{ marginBottom: 18 }}>
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

          <button className="primary" type="submit" disabled={busy}
            style={{ width: "100%", marginTop: 18, padding: "10px" }}>
            {busy && <span className="spin" />}{mode === "login" ? "登录" : "注册并登录"}
          </button>
        </form>

        {err && <div className="msg-err" style={{ marginTop: 12 }}>{err}</div>}
        <div className="muted" style={{ marginTop: 16, fontSize: 12.5, lineHeight: 1.7 }}>
          登录后请到「模型设置」配置你自己的模型 key。每个账号的 key 相互独立,不共用。
        </div>
      </div>
    </div>
  );
}
