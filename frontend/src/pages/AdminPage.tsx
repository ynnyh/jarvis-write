// 后台管理页(仅管理员):用户列表 + 多邀请码管理 + AI 味检测调参
import { useCallback, useEffect, useState } from "react";
import { api, AdminUser, AiFlavorConfig, FeatureUsageStat, InviteCodeItem, QualityOverview } from "../api";
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

      <UsageStatsCard />

      <QualityOverviewCard />

      {msg && <div className="msg-ok page-flash">{msg}</div>}
      {err && <div className="msg-err page-flash">{err}</div>}
    </>
  );
}

// 功能使用统计:各功能线的 使用人数/动作次数/最后使用时间。
// 回答「哪个工坊真的有人在用」——功能取舍看数据,不看情怀。
const FEATURE_CN: Record<string, string> = {
  novel: "小说主线",
  drama: "漫剧工坊",
  promo: "宣传片工坊",
  clips: "情绪短片工坊",
  inspire: "灵感工坊",
  birthday: "生日祝福工坊",
  series: "系列短片工坊",
};

function UsageStatsCard() {
  const [stats, setStats] = useState<FeatureUsageStat[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.adminUsageStats()
      .then((r) => setStats(r.usage))
      .catch((e) => setErr(errMsg(e)));
  }, []);

  return (
    <div className="card">
      <div className="card-head"><h2>功能使用统计</h2></div>
      <p className="card-desc">
        动作级计数(建/改/删、生成),不含浏览与轮询;数据只存在本站数据库,不含任何用户内容。
      </p>
      {err && <div className="msg-err">{err}</div>}
      {stats === null ? (
        !err && <p className="muted">加载中…</p>
      ) : stats.length === 0 ? (
        <p className="muted">还没有使用记录。</p>
      ) : (
        <div className="tbl-wrap mt-2">
          <table className="tbl">
            <thead>
              <tr><th>功能线</th><th>使用人数</th><th>动作次数</th><th>最后使用</th></tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.feature}>
                  <td>{FEATURE_CN[s.feature] ?? s.feature}</td>
                  <td>{s.users}</td>
                  <td>{s.uses}</td>
                  <td className="muted">
                    {s.last_used_at ? new Date(s.last_used_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// 生成质量观测:截断率 / 回炉画像 / 问题分布 / 章节体量。
// 全部来自既有落库(llm_usage / review_snapshot / chapter_issues),零额外成本。
// 回答「差评出在哪一类信号上」,与上面的功能使用账互补。
const TRIGGER_CN: Record<string, string> = {
  gate: "门禁回炉",
  review: "主审回炉",
  gate_degraded: "门禁降级隔离",
};
const ISSUE_TYPE_CN: Record<string, string> = {
  state: "状态/伤情",
  knowledge: "认知信息",
  timeline: "时间线",
  worldrule: "世界规则",
  ambient: "环境连续性",
  cast: "人物在场",
};
const FB_CAT_CN: Record<string, string> = {
  style_flavor: "文风 AI 味",
  fact_error: "事实/设定错误",
  pacing: "节奏",
  format_trunc: "格式/截断",
};

function QualityOverviewCard() {
  const [data, setData] = useState<QualityOverview | null>(null);
  const [err, setErr] = useState("");
  const [days, setDays] = useState(30);

  useEffect(() => {
    setData(null);
    api.adminQualityOverview(days)
      .then((r) => setData(r))
      .catch((e) => setErr(errMsg(e)));
  }, [days]);

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  return (
    <div className="card">
      <div className="card-head">
        <h2>生成质量观测</h2>
        <label className="muted" style={{ fontSize: "0.85em" }}>
          窗口{" "}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
            <option value={90}>近 90 天</option>
          </select>
        </label>
      </div>
      <p className="card-desc">
        生成侧四路信号聚合(截断/回炉/问题/体量),只读既有落库,零额外调用。
        差评集中时先看这里:截断率高是输出预算问题,降级隔离多是模型稳定性问题。
      </p>
      {err && <div className="msg-err">{err}</div>}
      {data === null ? (
        !err && <p className="muted">加载中…</p>
      ) : (
        <div className="mt-2">
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>信号</th><th>数值</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>LLM 调用</td>
                  <td>{data.llm.total_calls} 次</td>
                  <td className="muted">窗口内全部调用</td>
                </tr>
                <tr>
                  <td>截断率</td>
                  <td className={data.llm.truncated_ratio > 0.05 ? "msg-err" : ""}>
                    {pct(data.llm.truncated_ratio)}
                  </td>
                  <td className="muted">流式中途断流,读感为「内容戛然而止」</td>
                </tr>
                <tr>
                  <td>预算用尽</td>
                  <td>{pct(data.llm.finish_length_ratio)}</td>
                  <td className="muted">finish_reason=length,半截内容需续写</td>
                </tr>
                <tr>
                  <td>审校通过率</td>
                  <td>{data.rework.chapters_reviewed === 0 ? "—"
                    : pct(data.rework.pass_ratio)}</td>
                  <td className="muted">主审通过章 / 有快照章({data.rework.chapters_reviewed} 章)</td>
                </tr>
                <tr>
                  <td>平均回炉轮数</td>
                  <td>{data.rework.avg_revision_rounds}</td>
                  <td className="muted">
                    {Object.entries(data.rework.trigger_counts)
                      .map(([t, n]) => `${TRIGGER_CN[t] ?? t} ${n}`).join(" · ") || "无回炉"}
                  </td>
                </tr>
                <tr>
                  <td>门禁降级隔离</td>
                  <td className={data.rework.gate_degraded_count > 0 ? "msg-err" : ""}>
                    {data.rework.gate_degraded_count} 章
                  </td>
                  <td className="muted">LLM 失败/解析失败被隔离待人工,不是「写得差」</td>
                </tr>
                <tr>
                  <td>问题挂起</td>
                  <td>{data.issues.open_count}</td>
                  <td className="muted">
                    {Object.entries(data.issues.by_type)
                      .map(([t, n]) => `${ISSUE_TYPE_CN[t] ?? t} ${n}`).join(" · ") || "无记录"}
                  </td>
                </tr>
                <tr>
                  <td>章节体量</td>
                  <td>{data.volume.chapters} 章 · 均 {data.volume.avg_word_count} 字</td>
                  <td className="muted">窗口内有更新的章节</td>
                </tr>
                <tr>
                  <td>用户反馈</td>
                  <td>{data.feedback.total === 0 ? "暂无"
                    : `👍 ${data.feedback.good} · 👎 ${data.feedback.bad}`}</td>
                  <td className="muted">
                    {Object.entries(data.feedback.by_category)
                      .map(([k, n]) => `${FB_CAT_CN[k] ?? k} ${n}`).join(" · ") || "差评四桶见左侧"}
                  </td>
                </tr>
                <tr>
                  <td>交叉归因</td>
                  <td>{data.feedback.cross.bad_chapters.count === 0 ? "—"
                    : `${pct(data.feedback.cross.bad_chapters.degraded_ratio)} vs ${pct(data.feedback.cross.baseline.degraded_ratio)}`}</td>
                  <td className="muted">
                    差评章降级隔离率 vs 全体基线;显著更高 → 差评主因在模型稳定性,接近 → 在内容本身
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
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
