// 后台管理页(仅管理员):用户列表 + 邀请码设置
import { useCallback, useEffect, useState } from "react";
import { api, AdminUser, InviteCodeState } from "../api";

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selfId, setSelfId] = useState<number | null>(null);
  const [invite, setInvite] = useState<InviteCodeState | null>(null);
  const [inviteInput, setInviteInput] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  // 行内操作态:重置密码 / 删除二次确认
  const [resettingId, setResettingId] = useState<number | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(() => {
    api.adminListUsers()
      .then(setUsers)
      .catch((e) => {
        // 非管理员访问:接口 403,直接展示无权限
        if (String(e).includes("403") || String(e).includes("管理员")) {
          setForbidden(true);
        } else {
          setErr(String(e));
        }
      });
    api.adminGetInviteCode()
      .then((s) => { setInvite(s); setInviteInput(s.code); })
      .catch(() => setInvite(null));
  }, []);

  useEffect(() => {
    api.me().then((u) => setSelfId(u.id)).catch(() => setSelfId(null));
    load();
  }, [load]);

  async function saveInvite() {
    setBusy(true); setErr(""); setMsg("");
    try {
      const s = await api.adminSetInviteCode(inviteInput.trim());
      setInvite(s); setInviteInput(s.code);
      setMsg(s.code ? "邀请码已保存" : "已关闭注册");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmReset(id: number) {
    if (newPassword.length < 6) { setErr("密码至少 6 位"); return; }
    if (new TextEncoder().encode(newPassword).length > 72) {
      setErr("密码过长:按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)");
      return;
    }
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminResetPassword(id, newPassword);
      setResettingId(null); setNewPassword("");
      setMsg("密码已重置");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(u: AdminUser) {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminSetActive(u.id, !u.is_active);
      setMsg(u.is_active ? `已禁用 ${u.username}` : `已启用 ${u.username}`);
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete(u: AdminUser) {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminDeleteUser(u.id);
      setDeletingId(null);
      setMsg(`已删除用户 ${u.username} 及其全部项目`);
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (forbidden) {
    return (
      <div className="card">
        <h2>无权限</h2>
        <p className="card-desc">后台管理仅对管理员开放。</p>
      </div>
    );
  }

  return (
    <>
      <div className="page-head"><h1>后台管理</h1></div>

      <div className="card">
        <div className="card-head"><h2>注册邀请码</h2></div>
        <p className="card-desc">
          新用户注册必须填写邀请码。数据库里设置后优先生效(覆盖环境变量);
          留空保存 = 关闭注册,任何人都无法再注册。
        </p>
        <div className="input-row narrow">
          <input
            type="text"
            value={inviteInput}
            placeholder="留空 = 关闭注册"
            onChange={(e) => setInviteInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") saveInvite(); }}
          />
          <button className="primary" disabled={busy} onClick={saveInvite}>
            {busy && <span className="spin" />}保存
          </button>
        </div>
        {invite && (
          <div className="meta-line">
            当前生效:{invite.code ? `「${invite.code}」` : "(已关闭注册)"}
            · 来源:{invite.source === "db" ? "数据库" : "环境变量"}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h2>用户列表</h2></div>
        <table className="tbl">
          <thead>
            <tr>
              <th>用户名</th>
              <th>状态</th>
              <th>注册时间</th>
              <th>项目数</th>
              <th>累计用量</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <UserRow
                key={u.id}
                u={u}
                isSelf={u.id === selfId}
                busy={busy}
                resetting={resettingId === u.id}
                newPassword={newPassword}
                deleting={deletingId === u.id}
                onStartReset={() => {
                  setDeletingId(null);
                  setResettingId(u.id);
                  setNewPassword("");
                  setErr("");
                }}
                onCancelReset={() => setResettingId(null)}
                onPasswordChange={setNewPassword}
                onConfirmReset={() => confirmReset(u.id)}
                onToggleActive={() => toggleActive(u)}
                onStartDelete={() => {
                  setResettingId(null);
                  setDeletingId(u.id);
                  setErr("");
                }}
                onCancelDelete={() => setDeletingId(null)}
                onConfirmDelete={() => confirmDelete(u)}
              />
            ))}
          </tbody>
        </table>
        {!users.length && !err && <div className="muted">加载中…</div>}
      </div>

      {msg && <div className="msg-ok">{msg}</div>}
      {err && <div className="msg-err">{err}</div>}
    </>
  );
}

function UserRow(props: {
  u: AdminUser;
  isSelf: boolean;
  busy: boolean;
  resetting: boolean;
  newPassword: string;
  deleting: boolean;
  onStartReset: () => void;
  onCancelReset: () => void;
  onPasswordChange: (v: string) => void;
  onConfirmReset: () => void;
  onToggleActive: () => void;
  onStartDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const { u, isSelf, busy } = props;
  const totalTokens = u.total_prompt_tokens + u.total_completion_tokens;
  const tokensText = totalTokens > 0
    ? `${(totalTokens / 1000).toFixed(1)}k tokens · ${u.total_calls} 次`
    : "—";

  return (
    <>
      <tr>
        <td>
          {u.username}
          {isSelf && <span className="badge">我</span>}
          {u.is_admin && <span className="badge">管理员</span>}
        </td>
        <td>
          {u.is_active
            ? <span className="badge ok">正常</span>
            : <span className="badge err">已禁用</span>}
        </td>
        <td>{u.created_at ? u.created_at.slice(0, 10) : "—"}</td>
        <td>{u.project_count}</td>
        <td title={`prompt ${u.total_prompt_tokens} + completion ${u.total_completion_tokens}`}>
          {tokensText}
        </td>
        <td>
          <div className="actions">
            <button className="btn-sm" disabled={busy} onClick={props.onStartReset}>
              重置密码
            </button>
            {!isSelf && (
              <>
                <button
                  className={`btn-sm${u.is_active ? " danger" : ""}`}
                  disabled={busy}
                  onClick={props.onToggleActive}
                >
                  {u.is_active ? "禁用" : "启用"}
                </button>
                <button className="btn-sm danger" disabled={busy} onClick={props.onStartDelete}>
                  删除
                </button>
              </>
            )}
          </div>
        </td>
      </tr>
      {props.resetting && (
        <tr>
          <td colSpan={6}>
            <div className="input-row narrow">
              <input
                type="password"
                autoFocus
                value={props.newPassword}
                placeholder="新密码,至少 6 位"
                onChange={(e) => props.onPasswordChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") props.onConfirmReset();
                  if (e.key === "Escape") props.onCancelReset();
                }}
              />
              <button className="btn-sm primary" disabled={busy} onClick={props.onConfirmReset}>
                确认重置
              </button>
              <button className="btn-sm" disabled={busy} onClick={props.onCancelReset}>
                取消
              </button>
            </div>
            <div className="hint">
              按 UTF-8 字节计不能超过 72 字节(中文约占 3 字节/字)
            </div>
          </td>
        </tr>
      )}
      {props.deleting && (
        <tr>
          <td colSpan={6}>
            <div className="notice notice-err" style={{ marginTop: 0 }}>
              <div>
                将删除用户 {u.username} 及其名下全部 {u.project_count} 个项目
                (大纲/正文/事实库等),不可恢复。确认删除?
              </div>
              <div className="actions mt-2">
                <button className="btn-sm danger" disabled={busy} onClick={props.onConfirmDelete}>
                  {busy && <span className="spin" />}确认删除
                </button>
                <button className="btn-sm" disabled={busy} onClick={props.onCancelDelete}>
                  取消
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
