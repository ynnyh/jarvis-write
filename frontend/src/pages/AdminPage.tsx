// 后台管理页(仅管理员):用户列表 + 多邀请码管理 + AI 味检测调参
import { useCallback, useEffect, useState } from "react";
import { api, AdminUser, AiFlavorConfig, InviteCodeItem } from "../api";
import { errMsg } from "../pollJob";
import { CopyBtn } from "../ui/copy";

// 8 位易读随机串(去掉易混淆的 0/O/1/I),中间加连字符
function randomCode(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const pick = () => chars[Math.floor(Math.random() * chars.length)];
  return Array.from({ length: 4 }, pick).join("") + "-" + Array.from({ length: 4 }, pick).join("");
}

const CODE_RE = /^[A-Za-z0-9-]{4,64}$/;

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selfId, setSelfId] = useState<number | null>(null);
  const [codes, setCodes] = useState<InviteCodeItem[]>([]);
  const [legacy, setLegacy] = useState<{ code: string; source: "db" | "env" } | null>(null);
  const [newCode, setNewCode] = useState("");
  const [newNote, setNewNote] = useState("");
  const [newMax, setNewMax] = useState("");
  const [deletingCodeId, setDeletingCodeId] = useState<number | null>(null);
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
        if (errMsg(e).includes("403") || errMsg(e).includes("管理员")) {
          setForbidden(true);
        } else {
          setErr(errMsg(e));
        }
      });
    api.adminListInviteCodes()
      .then((r) => { setCodes(r.items); setLegacy(r.legacy_fallback); })
      .catch(() => { setCodes([]); setLegacy(null); });
  }, []);

  useEffect(() => {
    api.me().then((u) => setSelfId(u.id)).catch(() => setSelfId(null));
    load();
  }, [load]);

  async function createInvite() {
    const code = newCode.trim();
    if (!CODE_RE.test(code)) { setErr("邀请码需为 4-64 位字母、数字或连字符"); return; }
    let maxUses: number | null = null;
    if (newMax.trim() !== "") {
      maxUses = Number(newMax.trim());
      if (!Number.isInteger(maxUses) || maxUses < 1) { setErr("次数限制需为 ≥1 的整数,留空表示不限"); return; }
    }
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminCreateInviteCode(code, newNote.trim(), maxUses);
      setNewCode(""); setNewNote(""); setNewMax("");
      setMsg(`邀请码 ${code} 已创建`);
      load();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleCode(c: InviteCodeItem) {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminSetInviteCodeActive(c.id, !c.is_active);
      setMsg(c.is_active ? `已停用 ${c.code}` : `已启用 ${c.code}`);
      load();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDeleteCode(c: InviteCodeItem) {
    setBusy(true); setErr(""); setMsg("");
    try {
      await api.adminDeleteInviteCode(c.id);
      setDeletingCodeId(null);
      setMsg(`已删除邀请码 ${c.code}`);
      load();
    } catch (e) {
      setErr(errMsg(e));
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
      setErr(errMsg(e));
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
      setErr(errMsg(e));
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
      setErr(errMsg(e));
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
          新用户注册必须填写邀请码。可建多个码,分别限次、停用;
          列表为空时回落旧版单一邀请码(app_settings / 环境变量),创建第一个码后旧码自动失效。
        </p>
        {legacy && (
          <div className="notice notice-warn mt-0">
            当前使用旧版单一邀请码(来自{legacy.source === "db" ? "数据库" : "环境变量"}):
            {legacy.code ? `「${legacy.code}」` : "(空,已关闭注册)"}。
            创建第一个邀请码后,旧码自动失效。
          </div>
        )}
        <div className="input-row mt-2">
          <input
            type="text"
            value={newCode}
            placeholder="邀请码,4-64 位字母/数字/连字符"
            onChange={(e) => setNewCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createInvite(); }}
          />
          <button className="btn-sm" disabled={busy} onClick={() => setNewCode(randomCode())}>
            随机生成
          </button>
        </div>
        <div className="input-row mt-2">
          <input
            type="text"
            value={newNote}
            placeholder="备注(可空):这个码发给谁"
            onChange={(e) => setNewNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createInvite(); }}
          />
          <input
            type="number"
            min={1}
            className="input-md"
            value={newMax}
            placeholder="次数限制(留空=不限)"
            onChange={(e) => setNewMax(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createInvite(); }}
          />
          <button className="primary" disabled={busy} onClick={createInvite}>
            {busy && <span className="spin" />}创建
          </button>
        </div>
        <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>邀请码</th>
              <th>备注</th>
              <th>已用/上限</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {codes.map((c) => (
              <InviteCodeRow
                key={c.id}
                c={c}
                busy={busy}
                deleting={deletingCodeId === c.id}
                onToggle={() => toggleCode(c)}
                onStartDelete={() => { setDeletingCodeId(c.id); setErr(""); }}
                onCancelDelete={() => setDeletingCodeId(null)}
                onConfirmDelete={() => confirmDeleteCode(c)}
              />
            ))}
          </tbody>
        </table>
        </div>
        {!codes.length && !legacy && <div className="muted">暂无邀请码</div>}
      </div>

      <div className="card">
        <div className="card-head"><h2>用户列表</h2></div>
        <div className="tbl-wrap">
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
        </div>
        {!users.length && !err && <div className="muted">加载中…</div>}
      </div>

      <FlavorConfigCard />

      {msg && <div className="msg-ok page-flash">{msg}</div>}
      {err && <div className="msg-err page-flash">{err}</div>}
    </>
  );
}

// AI 味检测调参:规则类别权重 + 自愈门槛,保存后立即生效(不重启)。
// 某类误伤(人类好文被标脏)就压权重,漏杀就抬;门槛是定稿自动去味的触发线。
function FlavorConfigCard() {
  const [cfg, setCfg] = useState<AiFlavorConfig | null>(null);
  const [gate, setGate] = useState("");
  // 类别 → 编辑中的权重值(string 便于空态处理)
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.adminGetAiFlavorConfig()
      .then((c) => {
        setCfg(c);
        setGate(String(c.gate_score));
      })
      .catch((e) => setErr(errMsg(e)));
  }, []);

  async function save() {
    if (!cfg) return;
    const gateVal = Number(gate.trim());
    if (!(gateVal >= 0 && gateVal <= 30)) {
      setErr("自愈门槛需在 0-30 之间(每千字 AI 味分,干净文本通常 <5)");
      return;
    }
    const weights: Record<string, number> = {};
    for (const c of cfg.categories) {
      const raw = (edits[c.category] ?? "").trim();
      if (raw === "") continue; // 留空 = 用默认
      const v = Number(raw);
      if (!(v >= 0 && v <= 5)) {
        setErr(`「${c.category}」权重需在 0-5 之间`);
        return;
      }
      weights[c.category] = v;
    }
    setBusy(true); setErr(""); setMsg("");
    try {
      const out = await api.adminPutAiFlavorConfig(gateVal, weights);
      setCfg(out);
      setEdits({});
      setMsg("AI 味检测配置已保存并生效");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (!cfg) {
    return (
      <div className="card">
        <div className="card-head"><h2>AI 味检测调参</h2></div>
        <div className="muted">{err ? `加载失败:${err}` : "加载中…"}</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head"><h2>AI 味检测调参</h2></div>
      <p className="card-desc">
        生成时的 AI 味检测与自动去味重写的参数:某类误伤(人类好文被标脏)就压权重,
        漏杀就抬;「自愈门槛」是定稿 AI 味分数超过即触发自动重写的线。
        保存后立即生效,无需重启。
      </p>
      <div className="form-grid mt-2">
        <div className="field">
          <label className="fl">自愈门槛(每千字)</label>
          <input
            type="number" min={0} max={30} step={0.5} className="input-sm"
            value={gate}
            onChange={(e) => setGate(e.target.value)}
          />
          <div className="field-note">干净文本通常 &lt;5,套话偏多会到 6+;调高触发更少,调低更敏感</div>
        </div>
      </div>
      <div className="tbl-wrap mt-2">
        <table className="tbl">
          <thead>
            <tr>
              <th>规则类别</th>
              <th>出厂权重</th>
              <th>当前权重</th>
              <th>规则数</th>
            </tr>
          </thead>
          <tbody>
            {cfg.categories.map((c) => (
              <tr key={c.category}>
                <td>{c.category}</td>
                <td className="muted">{c.default_weight}</td>
                <td>
                  <input
                    type="number" min={0} max={5} step={0.1} className="input-sm"
                    placeholder={String(cfg.weights[c.category] ?? c.default_weight)}
                    value={edits[c.category] ?? ""}
                    onChange={(e) => setEdits({ ...edits, [c.category]: e.target.value })}
                  />
                </td>
                <td className="muted">{c.rules}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy} onClick={save}>
          {busy && <span className="spin" />}保存并生效
        </button>
        <span className="muted">权重留空 = 沿用当前值;改回与出厂相同即恢复默认</span>
      </div>
      {msg && <div className="msg-ok">{msg}</div>}
      {err && <div className="msg-err">{err}</div>}
    </div>
  );
}

function InviteCodeRow(props: {
  c: InviteCodeItem;
  busy: boolean;
  deleting: boolean;
  onToggle: () => void;
  onStartDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const { c, busy } = props;
  const usedUp = c.max_uses != null && c.used_count >= c.max_uses;
  const status = !c.is_active
    ? <span className="badge err">已停用</span>
    : usedUp
      ? <span className="badge warn">已用完</span>
      : <span className="badge ok">有效</span>;

  return (
    <>
      <tr>
        <td><code>{c.code}</code></td>
        <td>{c.note || "—"}</td>
        <td>{c.used_count}/{c.max_uses ?? "不限"}</td>
        <td>{status}</td>
        <td>
          <div className="actions">
            {/* 邀请码是要发给别人的,给一枚一键复制,别让人手选 */}
            <CopyBtn text={c.code} label="复制码" />
            <button
              className={`btn-sm${c.is_active ? " danger" : ""}`}
              disabled={busy}
              onClick={props.onToggle}
            >
              {c.is_active ? "停用" : "启用"}
            </button>
            <button className="btn-sm danger" disabled={busy} onClick={props.onStartDelete}>
              删除
            </button>
          </div>
        </td>
      </tr>
      {props.deleting && (
        <tr>
          <td colSpan={5}>
            <div className="notice notice-err mt-0">
              <div>
                删除邀请码 {c.code} 后,持有该码的人将无法再注册(不影响已注册用户)。确认删除?
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
            <div className="notice notice-err mt-0">
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
